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
    print(("OK    " if condition else "ERRORE") + "  " + name)


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

[Altro]
Type = NonToccare
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

    new = os.path.join(base, "unity6-nuovo")
    os.makedirs(new)
    check("Unity 6 senza configurazione: impostato", adapter_unity.bepinex_entrypoint(new, "6000.6.0f1"))
    entries = _section(read_file(cfg(new)), "Preloader.Entrypoint")
    check("Unity 6 senza configurazione: Type = MonoBehaviour con Assembly e Method",
              "Type = MonoBehaviour" in entries and "Assembly = UnityEngine.CoreModule.dll" in entries
              and "Method = .cctor" in entries)

    existing = os.path.join(base, "unity6-esistente")
    write(cfg(existing), CFG_BEPINEX)
    check("Unity 6 con configurazione di BepInEx: impostato",
              adapter_unity.bepinex_entrypoint(existing, "6000.3.12f1"))
    text = read_file(cfg(existing))
    entries = _section(text, "Preloader.Entrypoint")
    check("configurazione esistente: un solo Type, = MonoBehaviour",
              [v for v in entries if v.startswith("Type")] == ["Type = MonoBehaviour"])
    check("configurazione esistente: le altre righe restano (commenti, Assembly, Method, altre sezioni)",
              "# Default value: Application" in entries and "Assembly = UnityEngine.CoreModule.dll" in entries
              and "Method = .cctor" in entries and "EnableAssemblyCache = true" in _section(text, "Caching")
              and "Enabled = true" in _section(text, "Logging.Disk")
              and _section(text, "Altro") == ["Type = NonToccare"])
    adapter_unity.bepinex_entrypoint(existing, "6000.3.12f1")
    check("configurazione esistente: ripetere non cambia nulla", read_file(cfg(existing)) == text)

    without_section = os.path.join(base, "unity6-senza-sezione")
    write(cfg(without_section), "[Caching]\nEnableAssemblyCache = true\n")
    adapter_unity.bepinex_entrypoint(without_section, "6000.0.23f1")
    text = read_file(cfg(without_section))
    check("configurazione senza la sezione: aggiunta in fondo, il resto resta",
              "Type = MonoBehaviour" in _section(text, "Preloader.Entrypoint")
              and _section(text, "Caching") == ["EnableAssemblyCache = true", ""])

    old = os.path.join(base, "unity2022")
    write(cfg(old), CFG_BEPINEX)
    check("Unity 2022: non si tocca", not adapter_unity.bepinex_entrypoint(old, "2022.3.10f1")
              and read_file(cfg(old)) == CFG_BEPINEX)
    empty_game = os.path.join(base, "unity2022-vuoto")
    os.makedirs(empty_game)
    check("Unity 2022 senza configurazione: nessun file creato",
              not adapter_unity.bepinex_entrypoint(empty_game, "2022.3.10f1") and not os.path.exists(cfg(empty_game)))
    check("versione sconosciuta: non si tocca", not adapter_unity.bepinex_entrypoint(empty_game, None)
              and not adapter_unity.bepinex_entrypoint(empty_game, "x.y") and not os.path.exists(cfg(empty_game)))


# --- Unity: IMGUI and XUnity configuration ---------------------------------------------------------

def unity_game(folder, code):
    """Fake Unity Mono game: Gioco.exe, Gioco_Data/Managed/Assembly-CSharp.dll with these bytes (None = without)."""
    managed = os.path.join(folder, "Gioco_Data", "Managed")
    os.makedirs(managed)
    with open(os.path.join(folder, "Gioco.exe"), "wb") as f:
        f.write(b"MZ")
    if code is not None:
        with open(os.path.join(managed, "Assembly-CSharp.dll"), "wb") as f:
            f.write(b"MZ\x00" + code + b"\x00fine")
    return folder


def test_imgui(base):
    imgui = unity_game(os.path.join(base, "imgui"), b"OnGUI\x00GUIStyle\x00GUILayout")
    check("IMGUI: OnGUI + GUIStyle -> si", adapter_unity.uses_imgui(imgui))
    check("IMGUI: OnGUI + GUIContent -> si",
              adapter_unity.uses_imgui(unity_game(os.path.join(base, "imgui-content"), b"OnGUI\x00GUIContent")))
    check("IMGUI: OnGUI da solo (un metodo che si chiama cosi') -> no",
              not adapter_unity.uses_imgui(unity_game(os.path.join(base, "solo-ongui"), b"OnGUI\x00Update")))
    check("IMGUI: interfaccia UGUI/TextMeshPro -> no",
              not adapter_unity.uses_imgui(unity_game(os.path.join(base, "ugui"), b"TextMeshProUGUI\x00GUIStyle")))
    check("IMGUI: senza Assembly-CSharp.dll -> no",
              not adapter_unity.uses_imgui(unity_game(os.path.join(base, "senza-codice"), None)))
    check("IMGUI: cartella che non e' un gioco Unity -> no", not adapter_unity.uses_imgui(base))
    # the interface in a module of the game (asmdef, like SunHaven.Core.dll or Mouse.dll), not in Assembly-CSharp
    module = unity_game(os.path.join(base, "imgui-modulo"), b"Update\x00TextMeshProUGUI")
    with open(os.path.join(module, "Gioco_Data", "Managed", "Gioco.Interfaccia.dll"), "wb") as f:
        f.write(b"MZ\x00OnGUI\x00GUILayout\x00fine")
    check("IMGUI: OnGUI + GUILayout in un modulo del gioco (non Assembly-CSharp) -> si",
              adapter_unity.uses_imgui(module))
    third_party = unity_game(os.path.join(base, "imgui-terzi"), b"Update\x00TextMeshProUGUI")
    for name in ("Sirenix.Utilities.dll", "UnityEngine.IMGUIModule.dll", "QFSW.QC.dll", "UnityGLTFScripts.dll"):
        with open(os.path.join(third_party, "Gioco_Data", "Managed", name), "wb") as f:
            f.write(b"MZ\x00OnGUI\x00GUIStyle\x00fine")
    check("IMGUI: OnGUI solo nel motore o in librerie di terzi (Sirenix, console, GLTF) -> no",
              not adapter_unity.uses_imgui(third_party))
    managed_folder = os.path.join(third_party, "Gioco_Data", "Managed")
    for name in ("Assembly-CSharp-firstpass.dll", "SunHaven.Core.dll", "Mouse.dll", "Newtonsoft.Json.dll",
                 "System.Core.dll", "mscorlib.dll", "I2Localization.dll", "Rewired_Core.dll", "Gioco.Editor.dll"):
        with open(os.path.join(managed_folder, name), "wb") as f:
            f.write(b"MZ")
    check("DLL del gioco: Assembly-CSharp per prime, poi i moduli del gioco; motore, .NET, terzi ed editor no",
              [os.path.basename(p) for p in adapter_unity.game_assembly(os.path.join(third_party, "Gioco_Data"))]
              == ["Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll", "Mouse.dll", "SunHaven.Core.dll"])
    # old Unity games written in UnityScript: their code is Assembly-UnityScript (almost always with OnGUI);
    # UnityScript.Lang.dll and Boo.Lang.dll are the language's libraries
    old = unity_game(os.path.join(base, "imgui-unityscript"), None)
    managed_folder = os.path.join(old, "Gioco_Data", "Managed")
    for name, byte in (("Assembly-UnityScript.dll", b"MZ\x00OnGUI\x00GUIStyle\x00fine"),
                       ("Assembly-UnityScript-firstpass.dll", b"MZ"), ("UnityScript.Lang.dll", b"MZ"),
                       ("Boo.Lang.dll", b"MZ\x00OnGUI\x00GUIStyle\x00fine")):
        with open(os.path.join(managed_folder, name), "wb") as f:
            f.write(byte)
    check("DLL del gioco: Assembly-UnityScript (giochi vecchi) si', le librerie di UnityScript e Boo no; IMGUI "
              "in Assembly-UnityScript -> si",
              [os.path.basename(p) for p in adapter_unity.game_assembly(os.path.join(old, "Gioco_Data"))]
              == ["Assembly-UnityScript.dll", "Assembly-UnityScript-firstpass.dll"]
              and adapter_unity.uses_imgui(old))
    text = read_file(adapter_unity.configure(imgui, "en", "it"))
    check("configura: IMGUI acceso da solo per un gioco IMGUI",
              "[TextFrameworks]" in text and "EnableIMGUI=True" in text)
    ugui = os.path.join(base, "ugui")
    check("configura: nessuna chiave IMGUI per gli altri giochi",
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
    check("XUnity con Google: chiavi attese", v == GOOGLE_KEYS)
    check("XUnity: EnableBatching=False (gli invii a blocchi a Google falliscono e raddoppiano l'attesa)",
              v["Behaviour"]["EnableBatching"] == "False")
    v = adapter_unity._config_values("en", "it", imgui=True)
    check("XUnity con IMGUI: EnableIMGUI=True", v.get("TextFrameworks") == {"EnableIMGUI": "True"})
    url = "http://127.0.0.1:47123/translate"
    v = adapter_unity._config_values("ja", "it", url=url)
    check("XUnity con il traduttore di PatchSmith: CustomTranslate, indirizzo, niente attese ne' anti-spam, "
              "testi lunghi, nessun ripiego su Google",
              v["Service"] == {"Endpoint": "CustomTranslate", "FallbackEndpoint": ""}
              and v["Custom"] == dict(CUSTOM_KEYS, Url=url)
              and v["Behaviour"]["MaxCharactersPerTranslation"] == "2500"
              and v["General"] == {"Language": "it", "FromLanguage": "ja"} and "TextFrameworks" not in v)
    v = adapter_unity._config_values("en", "it", google=False)
    check("XUnity senza traduttore di PatchSmith e con Google in pausa: Endpoint vuoto (nessun traduttore)",
              v["Service"]["Endpoint"] == "" and "Custom" not in v)
    # in the file: all the keys, only once, in their section
    game = os.path.join(base, "config-xunity")
    os.makedirs(game)
    text = read_file(adapter_unity.configure(game, "en", "it", imgui=True, url=url))
    expectations = adapter_unity._config_values("en", "it", imgui=True, url=url)
    check("configura: ogni chiave scritta una volta nella sua sezione", all(
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
    check("CRC: impronta controllata (UTF-16, come la scrive Unity) -> si",
              adapter_unity_loc.checked_crc(with_crc) is True)
    odd = _catalog(os.path.join(base, "crc-dispari"), b"\x07" + (options % 2737410034).encode("utf-16-le"))
    check("CRC: impronta controllata anche con il testo UTF-16 a una posizione dispari -> si",
              adapter_unity_loc.checked_crc(odd) is True)
    utf8 = _catalog(os.path.join(base, "crc-utf8"), (options % 12345).encode("utf-8"))
    check("CRC: impronta controllata (testo semplice) -> si", adapter_unity_loc.checked_crc(utf8) is True)
    zero = _catalog(os.path.join(base, "crc-zero"), (options % 0).encode("utf-16-le") + (options % 0).encode("utf-8"))
    check("CRC: m_Crc 0 (non controllata) -> no", adapter_unity_loc.checked_crc(zero) is False)
    unlabeled = _catalog(os.path.join(base, "crc-senza"), b"")
    check("CRC: nessuna opzione -> no", adapter_unity_loc.checked_crc(unlabeled) is False)
    no_live = os.path.join(base, "crc-nessun-catalogo", "StandaloneWindows64", "x.bundle")
    write(no_live, "")
    check("CRC: catalogo mancante -> non si sa (None)", adapter_unity_loc.checked_crc(no_live) is None)
    broken = _catalog(os.path.join(base, "crc-rotto"), b"")
    write(os.path.join(base, "crc-rotto", "catalog.json"), "{non e' json")
    check("CRC: catalogo illeggibile -> non si sa (None)", adapter_unity_loc.checked_crc(broken) is None)
    broken_base64 = _catalog(os.path.join(base, "crc-base64"), b"")
    write(os.path.join(base, "crc-base64", "catalog.json"), json.dumps({"m_ExtraDataString": "@@@ non base64 @@@"}))
    check("CRC: dati extra non in base64 -> non si sa (None)",
              adapter_unity_loc.checked_crc(broken_base64) is None)


# --- number check: '2.5' -----------------------------------------------------------------------------

def test_decimals():
    from patchsmith import corrections
    from patchsmith.project import _to_translate
    check("numeri con k/M/B: '50k' = '50.000', '1.5M' = '1.500.000' (nessun avviso); '5m' non sono milioni; un "
              "numero diverso dice quale",
              not backend.translation_problems("Delivers 50k iridium", "Consegna 50.000 iridio", "it")
              and not corrections.warnings("Delivers 50k iridium\nto win", "Consegna 50.000 iridio\nper vincere")
              and not corrections.warnings("Speed 1.5M", "Velocita' 1.500.000")
              and not corrections.warnings("Run 5m", "Corri 5 metri")
              and corrections.warnings("Delivers 50k iridium", "Consegna 5.000 iridio")
              == ["different numbers from the original (missing 50k; extra 5.000)"])
    check("numeri con simboli e unita' ('$5k', '+$0,01/s', 'x 2', 'v1.0') non si traducono; le parole si' "
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
    check("'2.5' scritto in lettere: nessun errore, non vale come numero in lettere",
              error is None and missing_one is False and additional is False)
    check("'2.5' perso nella traduzione: scartata come numeri diversi", problems and "different numbers" in problems)
    check("'2.5' con la virgola italiana: traduzione buona",
              not backend.translation_problems("Speed x2.5 for 10 seconds", "Velocità x2,5 per 10 secondi", "it"))
    check("interi in lettere: ancora riconosciuti",
              not backend.translation_problems("You have 1 or more mods", "Hai uno o più mod", "it"))


# --- web games: key names -----------------------------------------------------------------------------

def _js_function(source, name):
    """The text of 'function nome(...) {...}' (braces counted: in the injector's functions there are none inside
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
    classes = re.search(r"var CLASSI_TASTO = (\[[^\]]*\]);", source)
    check("iniettore: classi dei tasti 'key' e 'kbd'",
              classes and {"key", "kbd"} <= set(json.loads(classes.group(1))))
    context = _js_function(source, "contestoTasto")
    tree_node = _js_function(source, "nodoTesto")
    check("iniettore: <kbd> e classi dei tasti fino al nonno",
              'nodeName === "KBD"' in context and "CLASSI_TASTO" in context and "i < 3" in context)
    check("iniettore: un testo dentro un tasto resta in originale", "contestoTasto(p)" in tree_node)
    check("Delete, Enter, Space, Home restano traducibili (altrove sono parole dell'interfaccia)",
              all(adapter_webui.is_ui_text(t) for t in ("Delete", "Enter", "Space", "Home")))
    check("Esc, Ctrl, Shift, F5: mai tradotti",
              not any(adapter_webui.is_ui_text(t) for t in ("Esc", "Ctrl", "Shift", "F5")))

    node = _node()
    if not node:
        print("(node.js non trovato: l'iniettore e' controllato solo nel codice)")
        return
    ignore = re.search(r"var IGNORA = \{[^}]*\};", source).group(0)
    script = "\n".join([
        'var mappa = new Map([["Delete", "Elimina"], ["Start Game", "Inizia partita"]]);',
        "var scrittiTesto = new WeakMap();", ignore, classes.group(0),
        _js_function(source, "traduci"), context, tree_node,
        "function el(nome, classi, padre) { return {nodeType: 1, nodeName: nome, parentNode: padre || null,",
        "  isContentEditable: false, hasAttribute: function () { return true; },",
        "  classList: {contains: function (c) { return classi.indexOf(c) >= 0; }}}; }",
        "function prova(testo, padre) { var n = {nodeType: 3, nodeValue: testo, parentNode: padre}; nodoTesto(n);",
        "  return n.nodeValue; }",
        'var corpo = el("BODY", []);',
        "console.log(JSON.stringify({",
        '  kbd: prova("Delete", el("KBD", [], corpo)),',
        '  classe_key: prova("Delete", el("SPAN", ["key"], corpo)),',
        '  dentro_kbd: prova("Delete", el("B", [], el("KBD", [], corpo))),',
        '  pulsante: prova("Delete", el("BUTTON", [], corpo)),',
        '  classe_simile: prova("Delete", el("SPAN", ["keyboard-help"], corpo)),',
        '  menu: prova("Start Game", el("BUTTON", [], corpo))}));',
    ])
    path = os.path.join(base, "tasti.js")
    write(path, script)
    r = subprocess.run([node, path], capture_output=True, timeout=60)
    try:
        outcome = json.loads(r.stdout.decode("utf-8"))
    except ValueError:
        outcome = {}
        print("   node:", r.stderr.decode("utf-8", "replace")[-500:])
    check("iniettore (node): 'Delete' in <kbd>, in class=\"key\" e dentro un <kbd> resta in originale",
              (outcome.get("kbd"), outcome.get("classe_key"), outcome.get("dentro_kbd")) == ("Delete",) * 3)
    check("iniettore (node): 'Delete' su un pulsante o in una classe solo simile si traduce",
              (outcome.get("pulsante"), outcome.get("classe_simile"), outcome.get("menu"))
              == ("Elimina", "Elimina", "Inizia partita"))


# --- real logs for the after-play check ---------------------------------------------------------------

# file -> signals it must contain (regular expressions)
EXPECTED_SIGNALS = {
    "unity-player.log": [r"Mono path\[0\] = '.+_Data/Managed'", r"Chainloader startup complete",
                         r"Loading \[Patchsmith traduttore locale", r"Initialize engine version: 6000\."],
    "unity-player-errore-google.log": [r"NullReferenceException", r"GoogleTranslateEndpointV2",
                                       r"\[Error  :XUnity\.AutoTranslator\] Failed: 'MASTER'"],
    "unity-crash-player.log": [r"^Crash!!!$", r"Application:\.cctor", r"GameObject:AddComponent",
                               r"BepInEx\.Bootstrap\.Chainloader:Start", r"Mono path\[0\]"],
    "bepinex-logoutput.log": [r"Chainloader startup complete", r"Loading \[XUnity Auto Translator"],
    # LogOutput.log of the session that ended in the crash: BepInEx started but never reached "startup complete"
    "bepinex-logoutput-crash.log": [r"Chainloader started", r"Loading \[XUnity Auto Translator",
                                    r"\A(?![\s\S]*startup complete)"],
    "servizio-xunity.log": [r"in ascolto su http://127\.0\.0\.1:\d+/translate \(gioco: ",
                            r"! traduzione interrotta", r"Il gioco si e' chiuso"],
    "unreal-dds.log": [r"Mounting pak file .*zz_Patchsmith_it_999_P\.pak",
                       r"Overriding language with game user settings language configuration option \(en\)"],
    # characters the TextMeshPro fonts lack (also outside the basic plane), IL2CPP game without "Mono path"
    "unity-tmp-caratteri-mancanti.log": [r"Unicode value \\u00D9 was not found in the \[slkscr\] font asset",
                                         r"Unicode value \\U0001F41A was not found", r"Discovering subsystems at path",
                                         r"\A(?![\s\S]*Mono path)"],
}
# personal data that must not be there: user name in paths, PC name, hardware data
PERSONAL = re.compile(r"Users[\\/](?!giocatore[\\/])|UserName: '(?!giocatore')|DESKTOP-|LogInit: (User|Computer):|"
                       r"VRAM:|Renderer:|7656119\d{10}", re.I)


def test_signals():
    present = sorted(f for f in os.listdir(SIGNALS) if f.endswith(".log"))
    check("registri veri: ci sono tutti", present == sorted(EXPECTED_SIGNALS))
    for name, expected_list in EXPECTED_SIGNALS.items():
        try:
            text = read_file(os.path.join(SIGNALS, name))
        except OSError:
            text = ""
        missing = [a for a in expected_list if not re.search(a, text, re.M)]
        check("%s: segnali presenti%s" % (name, " (mancano: %s)" % missing if missing else ""), not missing)
        personal = PERSONAL.findall(text)
        check("%s: anonimo%s" % (name, " (trovato: %s)" % personal[:3] if personal else ""),
                  text and not personal)
    # the other real signals of the after-play check: Windows event log entries and the translator's journal
    for name in ("evento-crash-1000.xml", "evento-crash-1026.xml", "diario-richieste.jsonl"):
        try:
            with open(os.path.join(SIGNALS, name), "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            text = ""
        personal = PERSONAL.findall(text) + re.findall(r"S-1-5-21-[\d-]+", text)
        check("%s: c'e' ed e' anonimo%s" % (name, " (trovato: %s)" % personal[:3] if personal else ""),
                  text and not personal)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "dati")  # none of these tests should write data, to be safe
    test_entrypoint(base)
    test_imgui(base)
    test_config_values(base)
    test_crc(base)
    test_decimals()
    test_keys(base)
    test_signals()
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
