"""Rilevamento del motore di gioco.

Usa le regole di SteamDB (FileDetectionRuleSets, licenza MIT, file data/rules.ini) sui percorsi
dei file, piu' controlli sui primi byte dei file e un secondo livello per i giochi web
(Electron / NW.js / HTML5). Ogni risultato ha un punteggio e l'elenco delle evidenze.
"""
import json
import os
import re
import struct

try:  # albero delle regex (modulo interno di Python 3.11): serve solo al prefiltro di _indice
    from re import _parser as _albero_re
    from re._constants import AT, AT_END, BRANCH, LITERAL, SUBPATTERN
except ImportError:
    _albero_re = None

CARTELLA_DATI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SEZIONI = ("Engine", "Container", "SDK", "AntiCheat", "Launcher", "Emulator", "Evidence")
PESI = {"Engine": 3, "Container": 3, "SDK": 1, "AntiCheat": 1, "Launcher": 1, "Emulator": 3, "Evidence": 1}
MAX_FILE = 200_000
MAX_PUNTI_REGOLE = 15  # tetto ai punti dati dalle regole sui nomi per ogni voce
LUNGHEZZA_CHIAVE = 12  # caratteri finali al massimo usati come chiave dal prefiltro per suffisso
# Installer di dipendenze inclusi in alcune copie del gioco: non dicono nulla sul motore.
CARTELLE_IGNORATE = {"_redist", "_commonredist", "redist", "redistributables", "directx", "vcredist"}

# Strada consigliata per motore (stato della Fase 0).
CONSIGLI = {
    "Web": "Supportato ora: adapter web-json (scansiona -> traduci).",
    "Electron": "Supportato ora: adapter web-json (scansiona -> traduci).",
    "GemShell": "Testi e codice in un archivio cifrato (GemShell): PatchSmith non forza le protezioni degli sviluppatori, "
    "quindi questo gioco non si puo' tradurre.",
    "NWJS": "Supportato ora: adapter web-json (scansiona -> traduci).",
    "RPGMaker": "MV/MZ: i dati sono JSON (www/data), l'adapter web-json funziona in via sperimentale. "
    "Altre versioni: in arrivo con rvpacker-txt-rs (Fase 1).",
    "Unity": "Supportato (Unity Mono): PatchSmith installa e configura BepInEx + XUnity.AutoTranslator, "
    "che traduce i testi dentro il gioco. Unity IL2CPP: supportato se usa Unity Localization (tabelle dei testi "
    "nei bundle), altrimenti in arrivo.",
    "RenPy": "In arrivo (Fase 1): framework tl/ di Ren'Py + unrpyc.",
    "Godot": "Supportato (Godot 4) se il gioco usa le tabelle delle traduzioni di Godot: PatchSmith legge la tabella "
    "inglese dal pacchetto dei dati (senza toccarlo) e aggiunge la traduzione accanto all'eseguibile (override.cfg).",
    "GameMaker": "Supportato se il gioco ha file di localizzazione esterni (es. Localization/english_*.txt): "
    "PatchSmith li traduce con backup. Testi scritti nel codice (data.win): in arrivo.",
    "Unreal": "Supportato (Unreal 4, pak non cifrati): PatchSmith traduce i file .locres e aggiunge un pak "
    "di patch. Unreal 5 / IoStore / pak cifrati: in arrivo.",
    "XNA": "Supportato se il gioco ha risorse .NET localizzate (.resources con satelliti): PatchSmith aggiunge "
    "satelliti tradotti senza toccare i file del gioco. Testi scritti nel codice: in arrivo.",
    "FNA": "Come XNA: risorse .NET localizzate supportate.",
    "MonoGame": "Come XNA: risorse .NET localizzate supportate.",
    "KiriKiri": "Al meglio (Fase 3): KirikiriTools / VNTextPatch.",
    "WolfRPGEditor": "Al meglio (Fase 3): UberWolf + WolfTL.",
}

# Firme nei primi byte: (regex sul percorso, firma, motore, descrizione)
FIRME = [
    (r"\.pck$", b"GDPC", "Godot", "archivio .pck Godot (GDPC)"),
    (r"(?:^|/)(?:data\.win|game\.unx|game\.ios)$", b"FORM", "GameMaker", "data.win GameMaker (FORM)"),
    (r"\.xp3$", b"XP3\r\n", "KiriKiri", "archivio .xp3 KiriKiri"),
    (r"\.rpa$", b"RPA-", "RenPy", "archivio .rpa Ren'Py"),
    (r"(?:^|/)(?:globalgamemanagers|data\.unity3d|[^/]+\.bundle)$", b"UnityFS", "Unity", "asset bundle Unity (UnityFS)"),
    (r"\.utoc$", b"-==--==--==--==-", "Unreal", "IoStore .utoc Unreal"),
]

FRAMEWORK_WEB = [
    ("Angular", re.compile(r"(?:^|/)polyfills[-.][\w-]*\.js$|(?:^|/)main[-.][A-Z0-9]{6,}\.js$"), b"ng-version|\xc9\xb5\xc9\xb5|@angular"),
    ("React", None, b"react-dom|__REACT_DEVTOOLS"),
    ("Vue", None, b"__VUE__|vue.runtime"),
    ("Phaser", re.compile(r"(?:^|/)phaser[\w.-]*\.js$", re.I), b"Phaser.Game"),
    ("PixiJS", re.compile(r"(?:^|/)pixi[\w.-]*\.js$", re.I), None),
    ("RPG Maker MV/MZ", re.compile(r"(?:^|/)js/(?:rpg|rmmz)_core\.js$", re.I), None),
    ("TyranoScript", re.compile(r"(?:^|/)tyrano\.js$", re.I), None),
    ("Construct", re.compile(r"(?:^|/)c[23]runtime\.js$|(?:^|/)c3main\.js$", re.I), None),
]


def carica_regole(percorso=None):
    percorso = percorso or os.path.join(CARTELLA_DATI, "rules.ini")
    regole = {s: [] for s in SEZIONI}
    sezione = None
    riga_re = re.compile(r"^([^=\[;]+?)(?:\[\])?\s*=\s*(.+?)(?:\s+;.*)?$")
    with open(percorso, encoding="utf-8") as f:
        for riga in f:
            riga = riga.strip()
            if not riga or riga.startswith(";"):
                continue
            if riga.startswith("[") and riga.endswith("]"):
                sezione = riga[1:-1]
                continue
            m = riga_re.match(riga)
            if not m or sezione not in regole:
                continue
            try:
                regole[sezione].append((m.group(1).strip(), re.compile(m.group(2), re.I)))
            except re.error:
                pass  # regola PCRE non supportata da Python: la saltiamo
    return regole


def _sequenze(albero, limite=256):
    """Sviluppa alternative e gruppi semplici: ogni sequenza e' un cammino possibile di un match."""
    sequenze = [[]]
    for op, av in albero:
        if op == BRANCH:
            varianti = [s for ramo in av[1] for s in _sequenze(ramo, limite)]
        elif op == SUBPATTERN and not av[1] and not av[2]:  # gruppo senza flag propri
            varianti = _sequenze(av[3], limite)
        else:
            varianti = [[(op, av)]]  # elemento opaco: quantificatori, classi, ancore, lookaround...
        sequenze = [s + v for s in sequenze for v in varianti]
        if len(sequenze) > limite:
            raise ValueError("troppe alternative")
    return sequenze


def _chiavi(rx):
    """Condizioni necessarie perche' rx.search() trovi qualcosa in un percorso ASCII senza a capo.

    Insieme di coppie ("fine", s) / ("contiene", s), con s in minuscolo: se la regex trova qualcosa,
    almeno una e' vera per il percorso in minuscolo. None se non si deducono: regex da provare sempre.
    """
    if _albero_re is None or rx.flags & re.MULTILINE:
        return None
    try:
        sequenze = _sequenze(_albero_re.parse(rx.pattern, rx.flags))
    except (re.error, ValueError, RecursionError):
        return None
    chiavi = set()
    for seq in sequenze:
        if seq and seq[-1] == (AT, AT_END):  # finisce con $: conta il letterale subito prima
            coda = ""
            for op, av in reversed(seq[:-1]):
                if op != LITERAL or av > 127:
                    break
                coda = chr(av).lower() + coda
            if coda:
                chiavi.add(("fine", coda[-LUNGHEZZA_CHIAVE:]))
                continue
        tratto = migliore = ""  # altrimenti il tratto letterale piu' lungo
        for op, av in seq:
            tratto = tratto + chr(av).lower() if op == LITERAL and av <= 127 else ""
            migliore = max(migliore, tratto, key=len)
        if not migliore:
            return None
        chiavi.add(("contiene", migliore))
    # Basta la condizione piu' debole: "fine x.dll" rende superflua "fine /x.dll".
    return {a for a in chiavi if not any(b != a and _implica(a, b) for b in chiavi)}


def _implica(a, b):
    """Vero se ogni percorso che soddisfa la chiave a soddisfa anche la chiave b."""
    (tipo_a, testo_a), (tipo_b, testo_b) = a, b
    if tipo_b == "fine":
        return tipo_a == "fine" and testo_a.endswith(testo_b)
    return testo_b in testo_a


def _indice(espressioni):
    """Prefiltro per provare molte regex su molti percorsi senza chiamarle tutte su ognuno.

    Restituisce una funzione che, dato un percorso, elenca in ordine crescente gli indici delle regex
    che potrebbero trovarvi qualcosa (le altre di sicuro no): vanno poi verificate con search().
    """
    per_fine, per_contenuto, sempre = {}, {}, []
    for i, rx in enumerate(espressioni):
        chiavi = _chiavi(rx)
        if chiavi is None:
            sempre.append(i)
            continue
        for tipo, testo in chiavi:
            (per_fine if tipo == "fine" else per_contenuto).setdefault(testo, []).append(i)
    lunghezze = sorted({len(t) for t in per_fine})
    contenuti = list(per_contenuto.items())
    filtro = re.compile("|".join(map(re.escape, per_contenuto))) if per_contenuto else None
    tutte = list(range(len(espressioni)))

    def candidate(percorso):
        # Le chiavi valgono per maiuscole/minuscole ASCII e con $ solo a fine stringa: negli altri
        # casi (rarissimi) si provano tutte le regex.
        if not percorso.isascii() or "\n" in percorso:
            return tutte
        p = percorso.lower()
        trovate = list(sempre)
        for n in lunghezze:
            trovate += per_fine.get(p[-n:], ())
        if filtro and filtro.search(p):
            for testo, indici in contenuti:
                if testo in p:
                    trovate += indici
        return sorted(set(trovate))

    return candidate


def _filtra(espressioni, elenco):
    """Per ogni regex, i percorsi di elenco (nello stesso ordine) in cui search() trova qualcosa."""
    candidate = _indice(espressioni)
    trovati = [[] for _ in espressioni]
    for percorso in elenco:
        for i in candidate(percorso):
            if espressioni[i].search(percorso):
                trovati[i].append(percorso)
    return trovati


def elenca_file(cartella):
    """Percorsi relativi con / come separatore."""
    elenco = []
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if d.lower() not in CARTELLE_IGNORATE)
        rel = os.path.relpath(radice, cartella).replace(os.sep, "/")
        prefisso = "" if rel == "." else rel + "/"
        for d in dirs:
            elenco.append(prefisso + d + "/")
        for f in files:
            elenco.append(prefisso + f)
            if len(elenco) >= MAX_FILE:
                return elenco, True
    return elenco, False


def _leggi_inizio(percorso, n=16):
    try:
        with open(percorso, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _leggi_fine(percorso, n=4):
    try:
        with open(percorso, "rb") as f:
            f.seek(0, 2)
            dimensione = f.tell()
            if dimensione < n:
                return b""
            f.seek(dimensione - n)
            return f.read(n)
    except OSError:
        return b""


def voci_asar(percorso):
    """Elenco dei file contenuti in un archivio .asar di Electron (solo intestazione)."""
    try:
        with open(percorso, "rb") as f:
            testa = f.read(16)
            dimensione = struct.unpack("<I", testa[12:16])[0]
            intestazione = json.loads(f.read(dimensione).decode("utf-8"))
    except (OSError, ValueError, struct.error, UnicodeDecodeError):
        return []
    voci = []

    def _visita(nodo, prefisso):
        for nome, figlio in nodo.get("files", {}).items():
            if "files" in figlio:
                _visita(figlio, prefisso + nome + "/")
            else:
                voci.append(prefisso + nome)

    _visita(intestazione, "")
    return voci


def _framework_web(cartella, elenco):
    """Riconosce i framework web nelle cartelle che contengono index.html (anche dentro .asar)."""
    trovati = {}
    radici = [p for p in elenco if p.lower().endswith("index.html") and "node_modules/" not in p]
    for asar in (p for p in elenco if p.lower().endswith(".asar")):
        for v in voci_asar(os.path.join(cartella, asar)):
            if v.lower().endswith("index.html") and "node_modules/" not in v:
                radici.append(asar + "!/" + v)
    per_file = iter(_filtra([re_file for _, re_file, _ in FRAMEWORK_WEB if re_file], elenco))
    for nome, re_file, firma_contenuto in FRAMEWORK_WEB:
        prove = [p for p in next(per_file) if "node_modules/" not in p] if re_file else []
        if not prove and firma_contenuto:
            firma = re.compile(firma_contenuto)
            for p in elenco:
                if not p.lower().endswith(".js") or "node_modules/" in p:
                    continue
                try:
                    if os.path.getsize(os.path.join(cartella, p)) > 8_000_000:
                        continue
                    with open(os.path.join(cartella, p), "rb") as f:
                        if firma.search(f.read()):
                            prove.append(p)
                            break
                except OSError:
                    continue
        if prove:
            trovati[nome] = prove[:3]
    return radici, trovati


def rileva(cartella):
    """Restituisce un dizionario con risultati, framework web, radici web e consigli."""
    regole = carica_regole()
    elenco, troncato = elenca_file(cartella)
    risultati = {}  # (sezione, nome) -> {"punteggio", "evidenze"}

    def _aggiungi(sezione, nome, evidenza, peso, da_regola=False):
        r = risultati.setdefault((sezione, nome), {"punteggio": 0, "evidenze": [], "da_regole": 0})
        if da_regola:
            peso = max(0, min(peso, MAX_PUNTI_REGOLE - r["da_regole"]))
            r["da_regole"] += peso
        r["punteggio"] += peso
        if len(r["evidenze"]) < 4 and evidenza not in r["evidenze"]:
            r["evidenze"].append(evidenza)

    tutte = [(s, n, rx) for s in SEZIONI for n, rx in regole[s]]
    candidate = _indice([rx for _, _, rx in tutte])
    for percorso in elenco:
        for i in candidate(percorso):  # stesso ordine (percorso, regola) del confronto con tutte
            sezione, nome, rx = tutte[i]
            if rx.search(percorso):
                _aggiungi(sezione, nome, percorso, PESI[sezione], da_regola=True)

    per_firma = _filtra([re.compile(re_percorso, re.I) for re_percorso, _, _, _ in FIRME], elenco)
    for (_, firma, motore, descrizione), percorsi in zip(FIRME, per_firma):
        for percorso in percorsi:
            if _leggi_inizio(os.path.join(cartella, percorso)).startswith(firma):
                _aggiungi("Engine", motore, "%s: %s" % (descrizione, percorso), 10)
                break

    # Godot con il pacchetto dei dati incorporato nell'eseguibile (esportazione "embed pck"): "GDPC" in fondo
    for percorso in elenco:
        if percorso.lower().endswith(".exe") and percorso.count("/") <= 1 and                 _leggi_fine(os.path.join(cartella, percorso)) == b"GDPC":
            _aggiungi("Engine", "Godot", "%s (pacchetto dei dati incorporato)" % percorso, 10)
            break

    # Unity: Mono o IL2CPP
    if any(p.lower().endswith("gameassembly.dll") for p in elenco):
        _aggiungi("SDK", "UnityIL2CPP", "GameAssembly.dll", 5)
    if any(p.lower().endswith("managed/assembly-csharp.dll") for p in elenco):
        _aggiungi("SDK", "UnityMono", "Managed/Assembly-CSharp.dll", 5)

    # Electron anche senza LICENSE.electron.txt (spesso tolto): i file di Chromium accanto a resources/app
    minuscoli = {p.lower() for p in elenco}
    if any(p.endswith("chrome_100_percent.pak") for p in minuscoli) and \
            any(p.endswith("v8_context_snapshot.bin") or p.endswith("snapshot_blob.bin") for p in minuscoli) and \
            any("resources/app" in p for p in minuscoli):
        _aggiungi("Container", "Electron", "file di Chromium e resources/app", 10)
    # archivi cifrati: i testi non si leggono senza togliere una protezione degli sviluppatori (non lo facciamo)
    for p in elenco:
        if p.lower().endswith("resources/app/gemshell-assets"):
            _aggiungi("Container", "GemShell", "%s (cifrato)" % p, 10)

    radici_web, framework = _framework_web(cartella, elenco)
    if radici_web:
        _aggiungi("Engine", "Web", "index.html: %s" % radici_web[0], 4 + len(framework))

    ordine_sezioni = {s: i for i, s in enumerate(("Engine", "Container", "Emulator", "AntiCheat", "SDK", "Launcher", "Evidence"))}
    ordinati = sorted(
        ({"sezione": s, "nome": n, "punteggio": v["punteggio"], "evidenze": v["evidenze"]}
         for (s, n), v in risultati.items()),
        key=lambda r: (ordine_sezioni.get(r["sezione"], 9), -r["punteggio"], r["nome"]),
    )
    motori = [r for r in ordinati if r["sezione"] in ("Engine", "Container", "Emulator")]
    migliore = max((r["punteggio"] for r in motori), default=0)
    consigli = []
    for r in motori:  # consigli solo per i motori con almeno meta' del punteggio migliore
        if r["punteggio"] * 2 >= migliore and r["nome"] in CONSIGLI and CONSIGLI[r["nome"]] not in consigli:
            consigli.append(CONSIGLI[r["nome"]])
    return {
        "motore_principale": motori[0]["nome"] if motori else None,
        "file_esaminati": len(elenco),
        "troncato": troncato,
        "risultati": ordinati,
        "radici_web": radici_web,
        "framework_web": framework,
        "consigli": consigli,
    }


CIFRATI = ("GemShell",)  # contenitori che cifrano i file del gioco: PatchSmith non li apre


def contenitore_cifrato(esito):
    """Il nome del contenitore che cifra i file del gioco (es. 'GemShell'), o None."""
    return next((r["nome"] for r in esito.get("risultati", ()) if r["sezione"] == "Container"
                 and r["nome"] in CIFRATI), None)


def stampa(esito, log=print):
    log("File esaminati: %d%s" % (esito["file_esaminati"], " (elenco troncato)" if esito["troncato"] else ""))
    if not esito["risultati"]:
        log("Nessun motore riconosciuto.")
    etichette = {"Engine": "Motore", "Container": "Contenitore", "SDK": "Componente", "AntiCheat": "ANTI-CHEAT",
                 "Launcher": "Launcher", "Emulator": "Emulatore", "Evidence": "Indizio"}
    if esito["motore_principale"]:
        log("Motore principale: %s" % esito["motore_principale"])
    elif esito["risultati"]:
        log("Motore non riconosciuto (probabilmente proprietario o non in elenco).")
    ha_motore = bool(esito["motore_principale"])
    componenti = 0
    for r in esito["risultati"]:
        if r["sezione"] == "Evidence" and ha_motore:
            continue  # gli indizi servono solo se il motore non e' stato riconosciuto
        if r["sezione"] in ("SDK", "Launcher"):
            componenti += 1
            if componenti > 8:
                continue
        log("  [%3d] %-12s %s" % (r["punteggio"], etichette.get(r["sezione"], r["sezione"]), r["nome"]))
        for e in r["evidenze"][:2]:
            log("          - %s" % e)
    if esito["framework_web"]:
        log("Framework web: " + ", ".join(esito["framework_web"]))
    if any(r["sezione"] == "AntiCheat" for r in esito["risultati"]):
        log("ATTENZIONE: anti-cheat rilevato. Non usare strumenti a iniezione su questo gioco.")
    if esito["consigli"]:
        log("Strada consigliata:")
        for c in esito["consigli"]:
            log("  * " + c)
