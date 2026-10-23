"""Safety net against problems already solved on real games coming back, without games and without network.

Usage: python -I test/test_regressions.py <empty_temp_folder>
Covers:
- Unity 6 (One Million Coins): BepInEx starts at the first MonoBehaviour (entrypoint_bepinex), the other versions are
  not touched, an existing configuration is kept;
- Unity games with the interface in IMGUI (usa_imgui) and keys written in XUnity's configuration (_valori_config);
- Unity Localization: catalog that checks the hash (CRC) of the bundles (crc_controllato);
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

from patchsmith import adapter_unity, adapter_unity_loc, adapter_webui, backend, project as progetto  # noqa: E402

ESITI = []
SEGNALI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "signals")


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def leggi(percorso):
    with open(percorso, encoding="utf-8") as f:
        return f.read()


def scrivi(percorso, testo):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "w", encoding="utf-8", newline="\n") as f:
        f.write(testo)


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


def _sezione(testo, nome):
    righe, dentro = [], False
    for riga in testo.splitlines():
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", riga)
        if m:
            dentro = m.group(1).strip() == nome
        elif dentro:
            righe.append(riga.strip())
    return righe


def prova_entrypoint(base):
    cfg = lambda g: os.path.join(g, *adapter_unity.CONFIG_BEPINEX.split("/"))  # noqa: E731

    nuovo = os.path.join(base, "unity6-nuovo")
    os.makedirs(nuovo)
    controlla("Unity 6 senza configurazione: impostato", adapter_unity.entrypoint_bepinex(nuovo, "6000.6.0f1"))
    voci = _sezione(leggi(cfg(nuovo)), "Preloader.Entrypoint")
    controlla("Unity 6 senza configurazione: Type = MonoBehaviour con Assembly e Method",
              "Type = MonoBehaviour" in voci and "Assembly = UnityEngine.CoreModule.dll" in voci
              and "Method = .cctor" in voci)

    esistente = os.path.join(base, "unity6-esistente")
    scrivi(cfg(esistente), CFG_BEPINEX)
    controlla("Unity 6 con configurazione di BepInEx: impostato",
              adapter_unity.entrypoint_bepinex(esistente, "6000.3.12f1"))
    testo = leggi(cfg(esistente))
    voci = _sezione(testo, "Preloader.Entrypoint")
    controlla("configurazione esistente: un solo Type, = MonoBehaviour",
              [v for v in voci if v.startswith("Type")] == ["Type = MonoBehaviour"])
    controlla("configurazione esistente: le altre righe restano (commenti, Assembly, Method, altre sezioni)",
              "# Default value: Application" in voci and "Assembly = UnityEngine.CoreModule.dll" in voci
              and "Method = .cctor" in voci and "EnableAssemblyCache = true" in _sezione(testo, "Caching")
              and "Enabled = true" in _sezione(testo, "Logging.Disk")
              and _sezione(testo, "Altro") == ["Type = NonToccare"])
    adapter_unity.entrypoint_bepinex(esistente, "6000.3.12f1")
    controlla("configurazione esistente: ripetere non cambia nulla", leggi(cfg(esistente)) == testo)

    senza_sezione = os.path.join(base, "unity6-senza-sezione")
    scrivi(cfg(senza_sezione), "[Caching]\nEnableAssemblyCache = true\n")
    adapter_unity.entrypoint_bepinex(senza_sezione, "6000.0.23f1")
    testo = leggi(cfg(senza_sezione))
    controlla("configurazione senza la sezione: aggiunta in fondo, il resto resta",
              "Type = MonoBehaviour" in _sezione(testo, "Preloader.Entrypoint")
              and _sezione(testo, "Caching") == ["EnableAssemblyCache = true", ""])

    vecchio = os.path.join(base, "unity2022")
    scrivi(cfg(vecchio), CFG_BEPINEX)
    controlla("Unity 2022: non si tocca", not adapter_unity.entrypoint_bepinex(vecchio, "2022.3.10f1")
              and leggi(cfg(vecchio)) == CFG_BEPINEX)
    vuoto = os.path.join(base, "unity2022-vuoto")
    os.makedirs(vuoto)
    controlla("Unity 2022 senza configurazione: nessun file creato",
              not adapter_unity.entrypoint_bepinex(vuoto, "2022.3.10f1") and not os.path.exists(cfg(vuoto)))
    controlla("versione sconosciuta: non si tocca", not adapter_unity.entrypoint_bepinex(vuoto, None)
              and not adapter_unity.entrypoint_bepinex(vuoto, "x.y") and not os.path.exists(cfg(vuoto)))


# --- Unity: IMGUI and XUnity configuration ---------------------------------------------------------

def gioco_unity(cartella, codice):
    """Fake Unity Mono game: Gioco.exe, Gioco_Data/Managed/Assembly-CSharp.dll with these bytes (None = without)."""
    managed = os.path.join(cartella, "Gioco_Data", "Managed")
    os.makedirs(managed)
    with open(os.path.join(cartella, "Gioco.exe"), "wb") as f:
        f.write(b"MZ")
    if codice is not None:
        with open(os.path.join(managed, "Assembly-CSharp.dll"), "wb") as f:
            f.write(b"MZ\x00" + codice + b"\x00fine")
    return cartella


def prova_imgui(base):
    imgui = gioco_unity(os.path.join(base, "imgui"), b"OnGUI\x00GUIStyle\x00GUILayout")
    controlla("IMGUI: OnGUI + GUIStyle -> si", adapter_unity.usa_imgui(imgui))
    controlla("IMGUI: OnGUI + GUIContent -> si",
              adapter_unity.usa_imgui(gioco_unity(os.path.join(base, "imgui-content"), b"OnGUI\x00GUIContent")))
    controlla("IMGUI: OnGUI da solo (un metodo che si chiama cosi') -> no",
              not adapter_unity.usa_imgui(gioco_unity(os.path.join(base, "solo-ongui"), b"OnGUI\x00Update")))
    controlla("IMGUI: interfaccia UGUI/TextMeshPro -> no",
              not adapter_unity.usa_imgui(gioco_unity(os.path.join(base, "ugui"), b"TextMeshProUGUI\x00GUIStyle")))
    controlla("IMGUI: senza Assembly-CSharp.dll -> no",
              not adapter_unity.usa_imgui(gioco_unity(os.path.join(base, "senza-codice"), None)))
    controlla("IMGUI: cartella che non e' un gioco Unity -> no", not adapter_unity.usa_imgui(base))
    # the interface in a module of the game (asmdef, like SunHaven.Core.dll or Mouse.dll), not in Assembly-CSharp
    modulo = gioco_unity(os.path.join(base, "imgui-modulo"), b"Update\x00TextMeshProUGUI")
    with open(os.path.join(modulo, "Gioco_Data", "Managed", "Gioco.Interfaccia.dll"), "wb") as f:
        f.write(b"MZ\x00OnGUI\x00GUILayout\x00fine")
    controlla("IMGUI: OnGUI + GUILayout in un modulo del gioco (non Assembly-CSharp) -> si",
              adapter_unity.usa_imgui(modulo))
    terzi = gioco_unity(os.path.join(base, "imgui-terzi"), b"Update\x00TextMeshProUGUI")
    for nome in ("Sirenix.Utilities.dll", "UnityEngine.IMGUIModule.dll", "QFSW.QC.dll", "UnityGLTFScripts.dll"):
        with open(os.path.join(terzi, "Gioco_Data", "Managed", nome), "wb") as f:
            f.write(b"MZ\x00OnGUI\x00GUIStyle\x00fine")
    controlla("IMGUI: OnGUI solo nel motore o in librerie di terzi (Sirenix, console, GLTF) -> no",
              not adapter_unity.usa_imgui(terzi))
    gestite = os.path.join(terzi, "Gioco_Data", "Managed")
    for nome in ("Assembly-CSharp-firstpass.dll", "SunHaven.Core.dll", "Mouse.dll", "Newtonsoft.Json.dll",
                 "System.Core.dll", "mscorlib.dll", "I2Localization.dll", "Rewired_Core.dll", "Gioco.Editor.dll"):
        with open(os.path.join(gestite, nome), "wb") as f:
            f.write(b"MZ")
    controlla("DLL del gioco: Assembly-CSharp per prime, poi i moduli del gioco; motore, .NET, terzi ed editor no",
              [os.path.basename(p) for p in adapter_unity.assembly_del_gioco(os.path.join(terzi, "Gioco_Data"))]
              == ["Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll", "Mouse.dll", "SunHaven.Core.dll"])
    # old Unity games written in UnityScript: their code is Assembly-UnityScript (almost always with OnGUI);
    # UnityScript.Lang.dll and Boo.Lang.dll are the language's libraries
    vecchio = gioco_unity(os.path.join(base, "imgui-unityscript"), None)
    gestite = os.path.join(vecchio, "Gioco_Data", "Managed")
    for nome, byte in (("Assembly-UnityScript.dll", b"MZ\x00OnGUI\x00GUIStyle\x00fine"),
                       ("Assembly-UnityScript-firstpass.dll", b"MZ"), ("UnityScript.Lang.dll", b"MZ"),
                       ("Boo.Lang.dll", b"MZ\x00OnGUI\x00GUIStyle\x00fine")):
        with open(os.path.join(gestite, nome), "wb") as f:
            f.write(byte)
    controlla("DLL del gioco: Assembly-UnityScript (giochi vecchi) si', le librerie di UnityScript e Boo no; IMGUI "
              "in Assembly-UnityScript -> si",
              [os.path.basename(p) for p in adapter_unity.assembly_del_gioco(os.path.join(vecchio, "Gioco_Data"))]
              == ["Assembly-UnityScript.dll", "Assembly-UnityScript-firstpass.dll"]
              and adapter_unity.usa_imgui(vecchio))
    testo = leggi(adapter_unity.configura(imgui, "en", "it"))
    controlla("configura: IMGUI acceso da solo per un gioco IMGUI",
              "[TextFrameworks]" in testo and "EnableIMGUI=True" in testo)
    ugui = os.path.join(base, "ugui")
    controlla("configura: nessuna chiave IMGUI per gli altri giochi",
              "EnableIMGUI" not in leggi(adapter_unity.configura(ugui, "en", "it")))


# keys that PatchSmith forces on XUnity: {section: {key: value}} (add new ones here)
CHIAVI_GOOGLE = {
    "Service": {"Endpoint": "GoogleTranslateV2", "FallbackEndpoint": ""},
    "General": {"Language": "it", "FromLanguage": "en"},
    "Behaviour": {"MaxCharactersPerTranslation": "500", "EnableBatching": "False",
                  # XUnity's debug options always off (point 2: the after-play check turns them back on)
                  "EnableTextPathLogging": "False", "EnableTranslationHelper": "False", "CopyToClipboard": "False"},
}
# with PatchSmith's translator (point 3): no XUnity anti-spam check, texts up to 2500 characters
CHIAVI_CUSTOM = {"EnableShortDelay": "True", "DisableSpamChecks": "True"}


def prova_valori_config(base):
    v = adapter_unity._valori_config("en", "it")
    controlla("XUnity con Google: chiavi attese", v == CHIAVI_GOOGLE)
    controlla("XUnity: EnableBatching=False (gli invii a blocchi a Google falliscono e raddoppiano l'attesa)",
              v["Behaviour"]["EnableBatching"] == "False")
    v = adapter_unity._valori_config("en", "it", imgui=True)
    controlla("XUnity con IMGUI: EnableIMGUI=True", v.get("TextFrameworks") == {"EnableIMGUI": "True"})
    url = "http://127.0.0.1:47123/translate"
    v = adapter_unity._valori_config("ja", "it", url=url)
    controlla("XUnity con il traduttore di PatchSmith: CustomTranslate, indirizzo, niente attese ne' anti-spam, "
              "testi lunghi, nessun ripiego su Google",
              v["Service"] == {"Endpoint": "CustomTranslate", "FallbackEndpoint": ""}
              and v["Custom"] == dict(CHIAVI_CUSTOM, Url=url)
              and v["Behaviour"]["MaxCharactersPerTranslation"] == "2500"
              and v["General"] == {"Language": "it", "FromLanguage": "ja"} and "TextFrameworks" not in v)
    v = adapter_unity._valori_config("en", "it", google=False)
    controlla("XUnity senza traduttore di PatchSmith e con Google in pausa: Endpoint vuoto (nessun traduttore)",
              v["Service"]["Endpoint"] == "" and "Custom" not in v)
    # in the file: all the keys, only once, in their section
    gioco = os.path.join(base, "config-xunity")
    os.makedirs(gioco)
    testo = leggi(adapter_unity.configura(gioco, "en", "it", imgui=True, url=url))
    attese = adapter_unity._valori_config("en", "it", imgui=True, url=url)
    controlla("configura: ogni chiave scritta una volta nella sua sezione", all(
        _sezione(testo, sez).count("%s=%s" % (k, val)) == 1 for sez, chiavi in attese.items()
        for k, val in chiavi.items()))


# --- Unity Localization: bundle CRC ------------------------------------------------------------------

def _catalogo(cartella, extra):
    """Addressables catalog.json next to the bundle folder; extra: bytes of m_ExtraDataString."""
    scrivi(os.path.join(cartella, "catalog.json"), json.dumps({
        "m_LocatorId": "AddressablesMainContentCatalog", "m_InternalIds": [],
        "m_ExtraDataString": base64.b64encode(extra).decode("ascii")}))
    bundle = os.path.join(cartella, "StandaloneWindows64", "localization-string-tables-english(en)_assets_all.bundle")
    scrivi(bundle, "")
    return bundle


def prova_crc(base):
    opzioni = '{"m_Hash":"8f2c","m_Crc":%d,"m_Timeout":0,"m_ChunkedTransfer":false}'
    con_crc = _catalogo(os.path.join(base, "crc-utf16"), b"\x07\x00" + (opzioni % 2737410034).encode("utf-16-le"))
    controlla("CRC: impronta controllata (UTF-16, come la scrive Unity) -> si",
              adapter_unity_loc.crc_controllato(con_crc) is True)
    dispari = _catalogo(os.path.join(base, "crc-dispari"), b"\x07" + (opzioni % 2737410034).encode("utf-16-le"))
    controlla("CRC: impronta controllata anche con il testo UTF-16 a una posizione dispari -> si",
              adapter_unity_loc.crc_controllato(dispari) is True)
    utf8 = _catalogo(os.path.join(base, "crc-utf8"), (opzioni % 12345).encode("utf-8"))
    controlla("CRC: impronta controllata (testo semplice) -> si", adapter_unity_loc.crc_controllato(utf8) is True)
    zero = _catalogo(os.path.join(base, "crc-zero"), (opzioni % 0).encode("utf-16-le") + (opzioni % 0).encode("utf-8"))
    controlla("CRC: m_Crc 0 (non controllata) -> no", adapter_unity_loc.crc_controllato(zero) is False)
    senza = _catalogo(os.path.join(base, "crc-senza"), b"")
    controlla("CRC: nessuna opzione -> no", adapter_unity_loc.crc_controllato(senza) is False)
    nessuno = os.path.join(base, "crc-nessun-catalogo", "StandaloneWindows64", "x.bundle")
    scrivi(nessuno, "")
    controlla("CRC: catalogo mancante -> non si sa (None)", adapter_unity_loc.crc_controllato(nessuno) is None)
    rotto = _catalogo(os.path.join(base, "crc-rotto"), b"")
    scrivi(os.path.join(base, "crc-rotto", "catalog.json"), "{non e' json")
    controlla("CRC: catalogo illeggibile -> non si sa (None)", adapter_unity_loc.crc_controllato(rotto) is None)
    base64_rotto = _catalogo(os.path.join(base, "crc-base64"), b"")
    scrivi(os.path.join(base, "crc-base64", "catalog.json"), json.dumps({"m_ExtraDataString": "@@@ non base64 @@@"}))
    controlla("CRC: dati extra non in base64 -> non si sa (None)",
              adapter_unity_loc.crc_controllato(base64_rotto) is None)


# --- number check: '2.5' -----------------------------------------------------------------------------

def prova_decimali():
    from patchsmith import corrections as correzioni
    from patchsmith.project import _da_tradurre
    controlla("numeri con k/M/B: '50k' = '50.000', '1.5M' = '1.500.000' (nessun avviso); '5m' non sono milioni; un "
              "numero diverso dice quale",
              not backend.problemi_traduzione("Delivers 50k iridium", "Consegna 50.000 iridio", "it")
              and not correzioni.avvisi("Delivers 50k iridium\nto win", "Consegna 50.000 iridio\nper vincere")
              and not correzioni.avvisi("Speed 1.5M", "Velocita' 1.500.000")
              and not correzioni.avvisi("Run 5m", "Corri 5 metri")
              and correzioni.avvisi("Delivers 50k iridium", "Consegna 5.000 iridio")
              == ["numeri diversi dall'originale (manca 50k; in più 5.000)"])
    controlla("numeri con simboli e unita' ('$5k', '+$0,01/s', 'x 2', 'v1.0') non si traducono; le parole si' "
              "('2/3 artifact set pieces', 'OK', 'Level 1')",
              not any(_da_tradurre(t) for t in ("$5k", "+$0,01/s", "x 2", "1920 x 1080", "v1.0", "$200m"))
              and all(_da_tradurre(t) for t in ("2/3 artifact set pieces", "OK", "Go", "Level 1")))
    try:
        mancante = backend._numeri_in_lettere("Wait 2.5 seconds", "Aspetta due secondi e mezzo", "it")
        in_piu = backend._numeri_in_lettere("Wait two seconds", "Aspetta 2.5 secondi", "it")
        problemi = backend.problemi_traduzione("Wait 2.5 seconds", "Aspetta due secondi e mezzo", "it")
        errore = None
    except ValueError as e:  # int('2.5'): One Million Coins stopped here
        mancante = in_piu = problemi = None
        errore = e
    controlla("'2.5' scritto in lettere: nessun errore, non vale come numero in lettere",
              errore is None and mancante is False and in_piu is False)
    controlla("'2.5' perso nella traduzione: scartata come numeri diversi", problemi and "numeri diversi" in problemi)
    controlla("'2.5' con la virgola italiana: traduzione buona",
              not backend.problemi_traduzione("Speed x2.5 for 10 seconds", "Velocità x2,5 per 10 secondi", "it"))
    controlla("interi in lettere: ancora riconosciuti",
              not backend.problemi_traduzione("You have 1 or more mods", "Hai uno o più mod", "it"))


# --- web games: key names -----------------------------------------------------------------------------

def _funzione_js(sorgente, nome):
    """The text of 'function nome(...) {...}' (braces counted: in the injector's functions there are none inside
    strings)."""
    inizio = sorgente.index("function %s(" % nome)
    livello, i = 0, sorgente.index("{", inizio)
    while True:
        if sorgente[i] == "{":
            livello += 1
        elif sorgente[i] == "}":
            livello -= 1
            if livello == 0:
                return sorgente[inizio:i + 1]
        i += 1


def _node():
    trovato = shutil.which("node")
    if trovato:
        return trovato
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and os.path.exists(os.path.join(base, "nodejs", "node.exe")):
            return os.path.join(base, "nodejs", "node.exe")
    return None


def prova_tasti(base):
    sorgente = leggi(os.path.join(os.path.dirname(adapter_webui.__file__), "injector.js"))
    classi = re.search(r"var CLASSI_TASTO = (\[[^\]]*\]);", sorgente)
    controlla("iniettore: classi dei tasti 'key' e 'kbd'",
              classi and {"key", "kbd"} <= set(json.loads(classi.group(1))))
    contesto = _funzione_js(sorgente, "contestoTasto")
    nodo = _funzione_js(sorgente, "nodoTesto")
    controlla("iniettore: <kbd> e classi dei tasti fino al nonno",
              'nodeName === "KBD"' in contesto and "CLASSI_TASTO" in contesto and "i < 3" in contesto)
    controlla("iniettore: un testo dentro un tasto resta in originale", "contestoTasto(p)" in nodo)
    controlla("Delete, Enter, Space, Home restano traducibili (altrove sono parole dell'interfaccia)",
              all(adapter_webui.e_testo_ui(t) for t in ("Delete", "Enter", "Space", "Home")))
    controlla("Esc, Ctrl, Shift, F5: mai tradotti",
              not any(adapter_webui.e_testo_ui(t) for t in ("Esc", "Ctrl", "Shift", "F5")))

    node = _node()
    if not node:
        print("(node.js non trovato: l'iniettore e' controllato solo nel codice)")
        return
    ignora = re.search(r"var IGNORA = \{[^}]*\};", sorgente).group(0)
    script = "\n".join([
        'var mappa = new Map([["Delete", "Elimina"], ["Start Game", "Inizia partita"]]);',
        "var scrittiTesto = new WeakMap();", ignora, classi.group(0),
        _funzione_js(sorgente, "traduci"), contesto, nodo,
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
    percorso = os.path.join(base, "tasti.js")
    scrivi(percorso, script)
    r = subprocess.run([node, percorso], capture_output=True, timeout=60)
    try:
        esito = json.loads(r.stdout.decode("utf-8"))
    except ValueError:
        esito = {}
        print("   node:", r.stderr.decode("utf-8", "replace")[-500:])
    controlla("iniettore (node): 'Delete' in <kbd>, in class=\"key\" e dentro un <kbd> resta in originale",
              (esito.get("kbd"), esito.get("classe_key"), esito.get("dentro_kbd")) == ("Delete",) * 3)
    controlla("iniettore (node): 'Delete' su un pulsante o in una classe solo simile si traduce",
              (esito.get("pulsante"), esito.get("classe_simile"), esito.get("menu"))
              == ("Elimina", "Elimina", "Inizia partita"))


# --- real logs for the after-play check ---------------------------------------------------------------

# file -> signals it must contain (regular expressions)
SEGNALI_ATTESI = {
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
PERSONALI = re.compile(r"Users[\\/](?!giocatore[\\/])|UserName: '(?!giocatore')|DESKTOP-|LogInit: (User|Computer):|"
                       r"VRAM:|Renderer:|7656119\d{10}", re.I)


def prova_segnali():
    presenti = sorted(f for f in os.listdir(SEGNALI) if f.endswith(".log"))
    controlla("registri veri: ci sono tutti", presenti == sorted(SEGNALI_ATTESI))
    for nome, attesi in SEGNALI_ATTESI.items():
        try:
            testo = leggi(os.path.join(SEGNALI, nome))
        except OSError:
            testo = ""
        mancanti = [a for a in attesi if not re.search(a, testo, re.M)]
        controlla("%s: segnali presenti%s" % (nome, " (mancano: %s)" % mancanti if mancanti else ""), not mancanti)
        personali = PERSONALI.findall(testo)
        controlla("%s: anonimo%s" % (nome, " (trovato: %s)" % personali[:3] if personali else ""),
                  testo and not personali)
    # the other real signals of the after-play check: Windows event log entries and the translator's journal
    for nome in ("evento-crash-1000.xml", "evento-crash-1026.xml", "diario-richieste.jsonl"):
        try:
            with open(os.path.join(SEGNALI, nome), "rb") as f:
                testo = f.read().decode("utf-8", "replace")
        except OSError:
            testo = ""
        personali = PERSONALI.findall(testo) + re.findall(r"S-1-5-21-[\d-]+", testo)
        controlla("%s: c'e' ed e' anonimo%s" % (nome, " (trovato: %s)" % personali[:3] if personali else ""),
                  testo and not personali)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    progetto.RADICE_DATI = os.path.join(base, "dati")  # none of these tests should write data, to be safe
    prova_entrypoint(base)
    prova_imgui(base)
    prova_valori_config(base)
    prova_crc(base)
    prova_decimali()
    prova_tasti(base)
    prova_segnali()
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
