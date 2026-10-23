"""Reading of Steam (read-only, standard library): libraries, installed games, official languages.

- libraryfolders.vdf and appmanifest_<appid>.acf are "KeyValues" text files (VDF);
- appcache/appinfo.vdf is the binary cache of the games' information: it also contains the supported
  languages (common/supported_languages), so we know offline whether a game has official Italian.
The language a Steam game uses is the one chosen for that game (Properties > General > Language), saved
in the appmanifest (UserConfig/language): the game asks Steam for it at startup.
"""
import bisect
import os
import re
import struct
import sys

# PatchSmith codes -> language names in the Steam API
NOMI_STEAM = {
    "it": "italian", "en": "english", "fr": "french", "de": "german", "es": "spanish", "pt": "portuguese",
    "pt-BR": "brazilian", "ru": "russian", "pl": "polish", "ja": "japanese", "ko": "koreana", "zh-CN": "schinese",
    "zh-TW": "tchinese", "nl": "dutch", "sv": "swedish", "da": "danish", "no": "norwegian", "fi": "finnish",
    "cs": "czech", "hu": "hungarian", "ro": "romanian", "el": "greek", "tr": "turkish", "uk": "ukrainian",
    "th": "thai", "vi": "vietnamese", "ar": "arabic", "id": "indonesian", "es-419": "latam", "bg": "bulgarian",
    "ms": "malay",
}


class ErroreSteam(ValueError):
    pass


# --- text KeyValues (VDF) ------------------------------------------------------------------------

def _token(testo):
    i, n = 0, len(testo)
    while i < n:
        c = testo[i]
        if c.isspace():
            i += 1
        elif testo.startswith("//", i):
            fine = testo.find("\n", i)
            i = n if fine < 0 else fine + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            j, pezzi = i + 1, []
            while j < n and testo[j] != '"':
                if testo[j] == "\\" and j + 1 < n:
                    pezzi.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(testo[j + 1], testo[j + 1]))
                    j += 2
                else:
                    pezzi.append(testo[j])
                    j += 1
            yield ("s", "".join(pezzi))
            i = j + 1
        else:
            j = i
            while j < n and not testo[j].isspace() and testo[j] not in '{}"':
                j += 1
            yield ("s", testo[i:j])
            i = j


def leggi_vdf(testo):
    """Text KeyValues -> nested dictionaries (the keys stay as they are written)."""
    pila = [{}]
    chiave = None
    for t in _token(testo):
        if t == "{":
            if chiave is None:
                raise ErroreSteam("VDF non valido: '{' senza chiave")
            nuovo = {}
            pila[-1][chiave] = nuovo
            pila.append(nuovo)
            chiave = None
        elif t == "}":
            if len(pila) == 1:
                raise ErroreSteam("VDF non valido: '}' in piu'")
            pila.pop()
        elif chiave is None:
            chiave = t[1]
        else:
            pila[-1][chiave] = t[1]
            chiave = None
    return pila[0]


def _senza_maiuscole(d, chiave):
    for k, v in d.items():
        if k.lower() == chiave.lower():
            return v
    return None


# --- installation and libraries -----------------------------------------------------------------

def cartella_steam():
    """Steam's folder (from the registry, then the usual paths) or None."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            for radice, chiave, nome in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                         (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(radice, chiave) as k:
                        valore = winreg.QueryValueEx(k, nome)[0]
                    if valore and os.path.isdir(valore):
                        return os.path.normpath(valore)
                except OSError:
                    continue
        except ImportError:
            pass
    for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(p):
            return p
    return None


def librerie(steam=None):
    """Folders of the Steam libraries (with 'steamapps')."""
    steam = steam or cartella_steam()
    if not steam:
        return []
    uscita = [steam]
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            dati = leggi_vdf(f.read())
        cartelle = _senza_maiuscole(dati, "libraryfolders") or {}
        for v in cartelle.values():
            percorso = v.get("path") if isinstance(v, dict) else v
            if isinstance(percorso, str) and os.path.isdir(percorso):
                uscita.append(os.path.normpath(percorso))
    except (OSError, ErroreSteam):
        pass
    visti, uniche = set(), []
    for p in uscita:
        chiave = os.path.normcase(os.path.abspath(p))
        if chiave not in visti:
            visti.add(chiave)
            uniche.append(os.path.normpath(p))
    return uniche


def _chiavi_percorso(p):
    """The comparable forms of a path: as written and resolved (junctions, links, 8.3 short names)."""
    forme = {os.path.normcase(os.path.abspath(p)).rstrip("\\/")}
    try:
        forme.add(os.path.normcase(os.path.realpath(p)).rstrip("\\/"))
    except (OSError, ValueError):
        pass
    return forme


def gioco_installato(cartella_gioco, steam=None):
    """If the folder is a game installed by Steam: {'appid', 'nome', 'manifesto', 'lingua'}; otherwise None."""
    gioco = _chiavi_percorso(cartella_gioco)
    for libreria in librerie(steam):
        app = os.path.join(libreria, "steamapps")
        try:
            nomi = os.listdir(app)
        except OSError:
            continue
        for nome in nomi:
            if not (nome.startswith("appmanifest_") and nome.endswith(".acf")):
                continue
            percorso = os.path.join(app, nome)
            try:
                with open(percorso, encoding="utf-8", errors="replace") as f:
                    stato = _senza_maiuscole(leggi_vdf(f.read()), "AppState") or {}
            except (OSError, ErroreSteam):
                continue
            installdir = stato.get("installdir") if isinstance(stato, dict) else None
            if not isinstance(installdir, str) or not installdir:
                continue
            cartella = os.path.join(app, "common", installdir)
            if any(g == d or g.startswith(d + os.sep) for g in gioco for d in _chiavi_percorso(cartella)):
                utente = stato.get("UserConfig") or {}
                montata = stato.get("MountedConfig") or {}
                return {"appid": stato.get("appid"), "nome": stato.get("name"), "manifesto": percorso,
                        "cartella": os.path.normcase(os.path.abspath(cartella)),
                        "lingua": utente.get("language") or montata.get("language")}
    return None


def in_esecuzione():
    """True if Steam is open (steam.exe), None if it cannot be known."""
    try:
        from .system import processi_windows
        return any(os.path.basename(p).lower() == "steam.exe" for p in processi_windows())
    except (OSError, AttributeError, ImportError):
        return None


# --- appinfo.vdf (binary) -----------------------------------------------------------------------

_V27, _V28, _V29 = 0x07564427, 0x07564428, 0x07564429
_CACHE = {}  # only one cache read: {'identita': (path, mtime, size), 'valore': (...)}


def _kv_binario(dati, pos, chiavi, profondita=0):
    """Binary KeyValues: type (0 section, 1 string, 2 int32, 3 float, 7 uint64, 8/11 end)."""
    if profondita > 64:
        raise ErroreSteam("KeyValues troppo annidati")
    uscita = {}
    while True:
        tipo = dati[pos]
        pos += 1
        if tipo in (8, 11):
            return uscita, pos
        if chiavi is not None:
            indice = struct.unpack_from("<i", dati, pos)[0]
            if not 0 <= indice < len(chiavi):
                raise ErroreSteam("chiave KeyValues fuori tabella: %d" % indice)
            nome = chiavi[indice]
            pos += 4
        else:
            fine = dati.index(b"\x00", pos)
            nome = dati[pos:fine].decode("utf-8", "replace")
            pos = fine + 1
        if tipo == 0:
            uscita[nome], pos = _kv_binario(dati, pos, chiavi, profondita + 1)
        elif tipo == 1:
            fine = dati.index(b"\x00", pos)
            uscita[nome] = dati[pos:fine].decode("utf-8", "replace")
            pos = fine + 1
        elif tipo in (2, 4, 6):
            uscita[nome] = struct.unpack_from("<i", dati, pos)[0]
            pos += 4
        elif tipo == 3:
            uscita[nome] = struct.unpack_from("<f", dati, pos)[0]
            pos += 4
        elif tipo == 7:
            uscita[nome] = struct.unpack_from("<Q", dati, pos)[0]
            pos += 8
        elif tipo == 10:
            uscita[nome] = struct.unpack_from("<q", dati, pos)[0]
            pos += 8
        else:
            raise ErroreSteam("tipo KeyValues sconosciuto: %d" % tipo)


def _indicizza(dati):
    """(keys of the table or None, {appid: (start of the KeyValues, end of the record)}) of appinfo.vdf."""
    if len(dati) < 16:
        raise ErroreSteam("appinfo.vdf troppo corto")
    magia = struct.unpack_from("<I", dati, 0)[0]
    if magia not in (_V27, _V28, _V29):
        raise ErroreSteam("formato di appinfo.vdf sconosciuto (0x%08x)" % magia)
    pos, chiavi, limite = 8, None, len(dati)
    if magia >= _V29:
        tabella = struct.unpack_from("<q", dati, 8)[0]
        if not 16 <= tabella <= len(dati) - 4:
            raise ErroreSteam("appinfo.vdf: tabella delle chiavi fuori dal file")
        pos, limite = 16, tabella
        n = struct.unpack_from("<I", dati, tabella)[0]
        chiavi, q = [], tabella + 4
        for _ in range(n):
            fine = dati.index(b"\x00", q)
            chiavi.append(dati[q:fine].decode("utf-8", "replace"))
            q = fine + 1
    # after appid and size: info_state, last_updated, token, sha1, change_number, (from v28) binary sha1
    testa = 4 + 4 + 8 + 20 + 4 + (20 if magia >= _V28 else 0)
    record = {}
    while pos + 8 <= limite:
        app, dimensione = struct.unpack_from("<II", dati, pos)
        if app == 0:
            break
        fine = pos + 8 + dimensione
        if fine > limite or dimensione <= testa:
            raise ErroreSteam("appinfo.vdf: record %d troncato" % app)
        record[app] = (pos + 8 + testa, fine)
        pos = fine
    return chiavi, record


def _appinfo(steam=None):
    """(bytes, keys, records, already read) of the Steam cache, read again only if the file changes; None if missing."""
    steam = steam or cartella_steam()
    if not steam:
        return None
    percorso = os.path.join(steam, "appcache", "appinfo.vdf")
    try:
        st = os.stat(percorso)
        identita = (os.path.normcase(os.path.abspath(percorso)), st.st_mtime_ns, st.st_size)
        if _CACHE.get("identita") == identita:
            return _CACHE["valore"]
        with open(percorso, "rb") as f:
            dati = f.read()
    except OSError:
        return None
    try:
        chiavi, record = _indicizza(dati)
    except ErroreSteam:
        raise
    except (struct.error, IndexError, ValueError, OverflowError) as e:
        raise ErroreSteam("appinfo.vdf illeggibile (%s)" % e) from e
    _CACHE.clear()
    _CACHE.update(identita=identita, valore=(dati, chiavi, record, {}))
    return _CACHE["valore"]


def _info_record(cache, app):
    dati, chiavi, record, letti = cache
    if app not in letti:
        inizio, fine = record[app]
        try:
            kv, _ = _kv_binario(dati[inizio:fine], 0, chiavi)
        except ErroreSteam:
            raise
        except (struct.error, IndexError, ValueError, RecursionError) as e:
            raise ErroreSteam("appinfo.vdf: gioco %d illeggibile (%s)" % (app, e)) from e
        letti[app] = kv.get("appinfo", kv)
    return letti[app]


def info_app(appid, steam=None):
    """Information about a game from the Steam cache (appinfo.vdf), or None. Raises ErroreSteam if the
    cache is unreadable."""
    cache = _appinfo(steam)
    if cache is None:
        return None
    try:
        app = int(appid)
    except (TypeError, ValueError):
        return None
    if app not in cache[2]:
        return None
    return _info_record(cache, app)


# --- game outside Steam recognized from its executables -----------------------------------------

# executables that do not identify a game: component installers, generic launchers, anti-cheat, crash reporters
_ESEGUIBILI_GENERICI = re.compile(
    r"^(vc_?redist.*|visualcppredist.*|dotnetfx.*|ndp\d.*|dxsetup|dxwebsetup|oalinst|physx.*|.*prereq.*|"
    r".*redist.*|.*setup.*|.*install.*|unins\d*|.*crashhandler.*|.*crashreport.*|.*crashpad.*|"
    r"start_protected_game|launcher|game|play|start|run|main|app|client|bootstrap|easyanticheat.*|battleye.*|"
    r"be_service.*|eac_?launcher|notification_helper|.*webhelper.*|cefprocess|dowser|.*updater.*)\.exe$", re.I)
_CARTELLE_NON_GIOCO = {"_redist", "_commonredist", "redist", "redistributables", "directx", "vcredist", "support",
                       "install", "__installer", "installer", "installers", "prereqs", "prerequisites",
                       "easyanticheat", "battleye", "commonredist"}
_NON_GIOCO = re.compile(r"\b(demo|prologue|playtest|trial|benchmark|dedicated server|sdk)\b", re.I)


def _eseguibili_cartella(cartella):
    """{lowercase relative path with '/'} of the executables up to two subfolders deep (without installers)."""
    uscita = set()
    for radice, dirs, files in os.walk(cartella):
        rel = os.path.relpath(radice, cartella)
        livello = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [] if livello >= 2 else [d for d in dirs if d.lower() not in _CARTELLE_NON_GIOCO]
        for f in files:
            if f.lower().endswith(".exe") and not _ESEGUIBILI_GENERICI.match(f):
                uscita.add((f if rel == "." else os.path.join(rel, f)).replace(os.sep, "/").lower())
    return uscita


def _percorso_avvio(valore):
    p = str(valore or "").strip().strip('"').replace("\\", "/").lower()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _semplice(testo):
    return re.sub(r"[^0-9a-z]", "", str(testo or "").lower())


def nomi_simili(a, b):
    """True if two folder/game names look alike (equal or one inside the other, letters and digits only)."""
    a, b = _semplice(a), _semplice(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) < 4:
        return a == b
    return a == b or a in b or b in a


def riconosci_da_eseguibili(cartella_gioco, steam=None):
    """Game NOT installed by Steam but known to Steam: compares the folder's executables with the launch paths
    of the games in the Steam cache (config/launch/*/executable), ignoring case.
    Cautious: no installers or generic names, an executable launched by several games does not count, no
    winner on a tie. {'appid', 'nome', 'eseguibile', 'installdir', 'cartella_simile'} or None."""
    eseguibili = _eseguibili_cartella(cartella_gioco)
    if not eseguibili:
        return None
    cache = _appinfo(steam)
    if cache is None:
        return None
    dati, _, record, _ = cache
    nomi = {e.rsplit("/", 1)[-1] for e in eseguibili}
    ordinati = sorted((inizio, fine, app) for app, (inizio, fine) in record.items())
    inizi = [x[0] for x in ordinati]
    basso = dati.lower()
    candidati, troppo_comuni = set(), set()
    for nome in nomi:
        cercato = nome.encode("utf-8") + b"\x00"
        i, volte = basso.find(cercato), 0
        while i >= 0:
            volte += 1
            if volte > 200:
                troppo_comuni.add(nome)
                break
            k = bisect.bisect_right(inizi, i) - 1
            if k >= 0 and i < ordinati[k][1]:
                candidati.add(ordinati[k][2])
            i = basso.find(cercato, i + 1)
    del basso
    nomi -= troppo_comuni
    avviano = {}        # executable name -> {appids of the games that launch it}
    corrispondenze = {}  # appid -> {launch paths that exist in the folder}
    descrizioni = {}
    base = os.path.basename(os.path.normpath(os.path.abspath(cartella_gioco))).lower()
    piu_giu = {e.split("/", 1)[1] for e in eseguibili if "/" in e}  # chosen folder = the one above the game
    for app in candidati:
        try:
            info = _info_record(cache, app)
        except ErroreSteam:
            continue
        comune = info.get("common") or {}
        if not isinstance(comune, dict):
            continue
        tipo = str(comune.get("type", "")).lower()
        nome_app = str(comune.get("name", ""))
        if tipo not in ("game", "") or _NON_GIOCO.search(nome_app):
            continue
        config = info.get("config") or {}
        avvii = config.get("launch") if isinstance(config, dict) else None
        if not isinstance(avvii, dict):
            continue
        for voce in avvii.values():
            percorso = _percorso_avvio(voce.get("executable") if isinstance(voce, dict) else None)
            nome = percorso.rsplit("/", 1)[-1]
            if not percorso.endswith(".exe") or nome not in nomi:
                continue
            avviano.setdefault(nome, set()).add(app)
            if (percorso in eseguibili or percorso in piu_giu or
                    (percorso.startswith(base + "/") and percorso[len(base) + 1:] in eseguibili)):
                corrispondenze.setdefault(app, set()).add(percorso)
                descrizioni[app] = (nome_app, config.get("installdir"))
    validi = {}
    for app, percorsi in corrispondenze.items():
        unici = {p for p in percorsi if len(avviano.get(p.rsplit("/", 1)[-1], ())) == 1}
        if unici:
            validi[app] = unici
    if not validi:
        return None
    classifica = sorted(validi.items(), key=lambda x: len(x[1]), reverse=True)
    if len(classifica) > 1 and len(classifica[0][1]) == len(classifica[1][1]):
        return None  # tie: better not to guess
    app, percorsi = classifica[0]
    nome_app, installdir = descrizioni[app]
    return {"appid": str(app), "nome": nome_app or None, "eseguibile": sorted(percorsi)[0],
            "installdir": installdir if isinstance(installdir, str) else None,
            "cartella_simile": nomi_simili(base, installdir) or nomi_simili(base, nome_app)}


# --- languages ------------------------------------------------------------------------------------

def selezionabili_da_info(info):
    """Languages that can be chosen in Steam (Properties > General > Language): the depots' 'baselanguages' plus
    the languages of the per-language depots. The other official languages are chosen in the game."""
    depots = info.get("depots") or {}
    if not isinstance(depots, dict):
        return set()
    base = depots.get("baselanguages")
    lingue = {x.strip().lower() for x in str(base).split(",") if x.strip()} if base else set()
    for depot in depots.values():
        if isinstance(depot, dict):
            config = depot.get("config")
            lingua_depot = config.get("language") if isinstance(config, dict) else None
            if lingua_depot:
                lingue.add(str(lingua_depot).lower())
    return lingue


def ufficiali_da_info(info):
    """{'italian': {'supported': True, 'subtitles': ..., 'full_audio': ...}, ...} or None if unknown."""
    comune = info.get("common") or {}
    if not isinstance(comune, dict):
        return None
    supportate = comune.get("supported_languages")
    if isinstance(supportate, dict) and supportate:
        uscita = {}
        for nome, dettagli in supportate.items():
            if isinstance(dettagli, dict):
                uscita[nome.lower()] = {k: str(v).lower() in ("1", "true") for k, v in dettagli.items()}
        return uscita
    elenco = comune.get("languages")
    if isinstance(elenco, dict) and elenco:
        return {nome.lower(): {"supported": str(v) == "1"} for nome, v in elenco.items()}
    return None


def lingue_selezionabili(appid, steam=None):
    info = info_app(appid, steam)
    return selezionabili_da_info(info) if info else None


def lingue_ufficiali(appid, steam=None):
    info = info_app(appid, steam)
    return ufficiali_da_info(info) if info else None
