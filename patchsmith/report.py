"""Report for asking for help ("Create report"): a .zip file on the Desktop with what is needed to understand what
happened with a game, without having to dig through folders.

It contains: the logs of the latest operations (logs/ folder of the data), the game profile, the settings
WITHOUT the DeepL key and email, the state of the translators, the engine detection, the BepInEx logs and the
XUnity configuration, the end of the log and of the sentence diary of the live translator, the logs of
Unity (Player.log, Player-prev.log and the last crash) and of Unreal (<progetto>.log), PatchSmith's manifests in the
game, the list of texts left untranslated with the reason (problemi.json), the fixes not yet written to the game
(Unity: da_scrivere.json), the letters of the fonts read (Unity Localization: font.json), the result of the after-play
check (<gioco>.controllo.json) and the PatchSmith version.
It contains no game files and no saves. In the texts the key and the email (even if they ended up in a log by chance)
become "<tolto>" and the Windows user folder becomes %USERPROFILE%.
"""
import datetime
import glob
import json
import os
import platform
import re
import sys
import tempfile
import urllib.parse
import zipfile

from . import __version__

CHIAVI_SEGRETE = ("chiave", "email", "password", "token", "key")  # parts of the setting names to remove
MAX_BYTE = 512 * 1024  # of each log the end is kept
MAX_REGISTRI = 5  # operation logs (the game's most recent)
# logs that do not belong to a game: window errors, installation of the local translator, check at startup
REGISTRI_GENERALI = ("interfaccia", "traduttore-locale", "controllo")
TOLTO = "<tolto>"


def cartella_desktop():
    """The user's Desktop, even if Windows has moved it (e.g. into OneDrive); if it cannot be found, PatchSmith's
    logs folder."""
    from .project import cartella_dati
    if sys.platform.startswith("win"):
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(1024)
            # CSIDL_DESKTOPDIRECTORY: the user's real Desktop folder
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) == 0 and \
                    os.path.isdir(buffer.value):
                return buffer.value
        except (AttributeError, OSError):
            pass
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else cartella_dati("logs")


def segreta(chiave):
    return any(s in str(chiave).lower() for s in CHIAVI_SEGRETE)


def impostazioni_pulite(dati):
    """The settings without keys, emails and the like."""
    return {k: v for k, v in dati.items() if not segreta(k)} if isinstance(dati, dict) else {}


def _segreti():
    """The values that must never get out: DeepL key, email (from the settings and from the environment variables)."""
    from .project import cartella_dati
    valori = set()
    try:
        with open(cartella_dati("settings.json"), encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        dati = {}
    if isinstance(dati, dict):
        valori |= {v.strip() for k, v in dati.items() if segreta(k) and isinstance(v, str)}
    valori |= {os.environ.get(v, "").strip() for v in ("PATCHSMITH_DEEPL", "PATCHSMITH_EMAIL")}
    return sorted((v for v in valori if len(v) >= 4), key=len, reverse=True)


def pulisci(testo, segreti):
    """Text without the secrets and with the user folder written as %USERPROFILE%."""
    for s in segreti:
        # also as it appears in a web address (e.g. the MyMemory email: 'de=nome%40dominio.it')
        for forma in dict.fromkeys((s, urllib.parse.quote(s, safe=""), urllib.parse.quote_plus(s))):
            testo = re.sub(re.escape(forma), TOLTO, testo, flags=re.I)
    casa = os.path.expanduser("~").rstrip("\\/")
    if len(casa) > 3:
        for forma in {casa, casa.replace("\\", "/")}:
            testo = re.sub(re.escape(forma), "%USERPROFILE%", testo, flags=re.I)
            testo = testo.replace(json.dumps(forma)[1:-1], "%USERPROFILE%")  # as it appears inside a JSON
    return testo


def _leggi(percorso, coda=True):
    with open(percorso, "rb") as f:
        if coda:
            f.seek(0, os.SEEK_END)
            dimensione = f.tell()
            f.seek(max(0, dimensione - MAX_BYTE))
            dati = f.read()
            if dimensione > MAX_BYTE:
                dati = ("[... tenuti solo gli ultimi %d KB di %d KB ...]\n" % (MAX_BYTE // 1024, dimensione // 1024)
                        ).encode("utf-8") + dati
        else:
            dati = f.read()
    if dati.startswith((b"\xff\xfe", b"\xfe\xff")):
        return dati.decode("utf-16", "replace")
    return dati.decode("utf-8-sig", "replace")


def registri_del_gioco(nomi, massimo=MAX_REGISTRI, generali=REGISTRI_GENERALI):
    """The logs of that game's most recent operations, recognized by the name of the game profile or of the folder
    at the end of the file name (if there are none: the most recent overall), plus the latest of each of the
    general logs (window errors, installation of the local translator, check at startup): so the
    general logs do not push out the game's, and vice versa."""
    from .project import cartella_dati
    nomi = [n for n in ([nomi] if isinstance(nomi, str) else nomi) if n and n not in generali]
    tutti = sorted(glob.glob(os.path.join(glob.escape(cartella_dati("logs")), "*.log")), key=_data, reverse=True)

    def di(n):
        return [p for p in tutti if os.path.basename(p)[:-4].endswith("-" + n)]

    suoi = [p for p in tutti if any(os.path.basename(p)[:-4].endswith("-" + n) for n in nomi)]
    scelti = (suoi or tutti)[:massimo]
    for g in generali:
        scelti += [p for p in di(g)[:1] if p not in scelti]
    return scelti


def _data(percorso):
    try:
        return os.path.getmtime(percorso)
    except OSError:
        return 0


def _unity(radice):
    """[(name in the zip, path)] of the Unity logs: Player.log and Player-prev.log (in LocalLow) and the Player.log
    of the last crash (in %TEMP%), found with the company and product written in <gioco>_Data/app.info."""
    from . import adapter_unity
    try:
        dati = adapter_unity.analizza(radice).get("dati")
        with open(os.path.join(dati, "app.info"), encoding="utf-8", errors="replace") as f:
            righe = [r.strip() for r in f.read().splitlines()]
    except Exception:  # noqa: BLE001 - not Unity or unreadable: the report is made anyway, without these
        return []
    if len(righe) < 2 or not righe[0] or not righe[1]:
        return []
    azienda, prodotto = righe[0], righe[1]
    utente = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    locallow = os.path.join(utente, "AppData", "LocalLow", azienda, prodotto)
    voci = [("unity/" + n, os.path.join(locallow, n)) for n in ("Player.log", "Player-prev.log")]
    crash = os.path.join(os.environ.get("TEMP") or tempfile.gettempdir(), azienda, prodotto, "Crashes")
    try:
        cartelle = sorted((os.path.join(crash, d) for d in os.listdir(crash)), key=_data, reverse=True)
    except OSError:
        cartelle = []
    for d in cartelle:
        if os.path.isfile(os.path.join(d, "Player.log")):
            voci.append(("unity/crash-%s-Player.log" % os.path.basename(d), os.path.join(d, "Player.log")))
            break
    return voci


def crea(cartella, destinazione=None, esito=None, registri=None, extra=None):
    """Creates the zip and returns its path. cartella: the game's folder (even empty: then only the general
    data); destinazione: where to write it (default: the Desktop); esito: the last outcome (esito.Esito);
    registri: the operation logs to include (default: the game's latest); extra: extra lines for
    the summary."""
    from . import adapter_unity, auto as automatico, ollama, detection as rilevamento
    from .project import cartella_dati, trova_info
    from .text import slug
    segreti = _segreti()
    cartella = os.path.abspath(cartella) if cartella and os.path.isdir(cartella) else None
    profilo_percorso = automatico.trova_profilo(cartella) if cartella else None
    profilo = {}
    if profilo_percorso:
        try:
            with open(profilo_percorso, encoding="utf-8") as f:
                profilo = json.load(f)
        except (OSError, ValueError):
            profilo = {}
    radice = profilo.get("cartella_gioco") or cartella
    radice = os.path.abspath(radice) if radice and os.path.isdir(radice) else cartella
    nome = (os.path.splitext(os.path.basename(profilo_percorso))[0] if profilo_percorso else
            slug(os.path.basename(cartella)) if cartella else "")
    testi = {}  # name in the zip -> text
    problemi = []

    def aggiungi(nome_zip, percorso, coda=True):
        if not percorso or not os.path.isfile(percorso):
            return
        try:
            testi[nome_zip] = _leggi(percorso, coda)
        except OSError as e:
            problemi.append("%s: %s" % (nome_zip, e))

    # the logs of the game, of the window errors outside an operation, of the installation of the local
    # translator and of the after-play check done at startup
    nomi = [nome] + [slug(os.path.basename(d)) for d in (cartella, radice) if d]
    for p in registri if registri is not None else registri_del_gioco(nomi):
        aggiungi("registri/" + os.path.basename(p), p)
    if profilo_percorso:
        aggiungi("profilo/" + os.path.basename(profilo_percorso), profilo_percorso, coda=False)
        # the after-play check: signals seen, fixes already tried and their outcome
        controllo = os.path.splitext(profilo_percorso)[0] + ".controllo.json"
        aggiungi("profilo/" + os.path.basename(controllo), controllo, coda=False)
    try:
        with open(cartella_dati("settings.json"), encoding="utf-8") as f:
            testi["settings.json"] = json.dumps(impostazioni_pulite(json.load(f)), ensure_ascii=False, indent=2)
    except (OSError, ValueError) as e:
        problemi.append("settings.json: %s" % e)
    aggiungi("services.json", cartella_dati("services.json"), coda=False)
    for n in ("servizio-xunity.log", "server.log"):
        aggiungi("traduttore-locale/" + n, os.path.join(ollama.cartella(), n))
    if nome:  # the sentences requested while playing (only game texts)
        from .xunity_service import percorso_diario
        aggiungi("traduttore-locale/" + os.path.basename(percorso_diario(nome)), percorso_diario(nome))
    if profilo.get("nome") and profilo.get("lingua_destinazione"):  # the texts left untranslated, with the reason
        from .project import percorso_problemi
        problemi_testi = percorso_problemi(profilo)
        aggiungi("traduzioni/" + os.path.basename(problemi_testi), problemi_testi, coda=False)
        # Unity: the fixes from "Fix translations" not yet written to the XUnity cache
        from .corrections import percorso_da_scrivere
        aggiungi("traduzioni/" + os.path.basename(percorso_da_scrivere(profilo)), percorso_da_scrivere(profilo),
                 coda=False)
    if profilo.get("nome"):  # Unity Localization: the letters the game's fonts can draw (font_unity.py)
        aggiungi("traduzioni/%s.font.json" % profilo["nome"],
                 cartella_dati("memory", "%s.unity" % profilo["nome"], "font.json"), coda=False)
    modificati = []
    if radice:
        try:
            righe = []
            rilevamento.stampa(rilevamento.rileva(radice), righe.append)
            testi["rilevamento.txt"] = "\n".join(righe)
        except Exception as e:  # the report is made anyway: it is exactly when something goes wrong that it is needed
            problemi.append("rilevamento: %s: %s" % (type(e).__name__, e))
        unity = adapter_unity.trova_radice(radice) or radice
        for rel in (adapter_unity.LOG_BEPINEX, adapter_unity.CONFIG_XUNITY, adapter_unity.CONFIG_BEPINEX,
                    adapter_unity.CONFIG_SERVIZIO, adapter_unity.STATO_SERVIZIO):
            aggiungi("gioco/" + rel, os.path.join(unity, *rel.split("/")))
        for d in dict.fromkeys((radice, unity)):
            for manifesto in ("patchsmith-unity.json", "patchsmith-unreal.json", "patchsmith-xna.json"):
                aggiungi("gioco/" + manifesto, os.path.join(d, manifesto), coda=False)
        for nome_zip, p in _unity(unity):
            aggiungi(nome_zip, p)
        # Unreal: the game's log, from which the after-play check understands whether the translation pak was
        # loaded and which language is chosen (its problems propose the report itself)
        try:
            from . import adapter_unreal, checks as controllo
            manifesto = adapter_unreal.leggi_manifesto(radice)
            for p in controllo._registri_unreal(radice, manifesto) if manifesto else []:
                aggiungi("unreal/" + os.path.basename(p), p)
        except Exception as e:  # noqa: BLE001 - the report is made anyway, without these
            problemi.append("registro di Unreal: %s: %s" % (type(e).__name__, e))
        try:
            modificati = [os.path.relpath(p, radice).replace(os.sep, "/") for p in trova_info(radice)]
        except OSError:
            pass
    riepilogo = [
        "Rapporto di PatchSmith %s" % __version__,
        "Creato: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Sistema: %s, Python %s" % (platform.platform(), platform.python_version()),
        "Cartella scelta: %s" % (cartella or "(nessuna)"),
        "Cartella del gioco: %s" % (radice or "(nessuna)"),
        "Profilo: %s (motore %s)" % (nome or "(nessuno)", profilo.get("motore") or "-"),
        "File del gioco modificati da PatchSmith: %s" % (", ".join(modificati[:50]) or "nessuno"),
    ]
    if esito is not None:
        riepilogo.append("Ultimo esito: %s" % json.dumps(esito.come_dizionario(), ensure_ascii=False, default=str))
    riepilogo += list(extra or [])
    if problemi:
        riepilogo.append("Non inclusi: " + "; ".join(problemi))
    riepilogo.append("Contenuto: " + ", ".join(sorted(testi)))
    testi["riepilogo.txt"] = "\n".join(riepilogo)

    destinazione = destinazione or cartella_desktop()
    os.makedirs(destinazione, exist_ok=True)
    adesso = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    percorso = os.path.join(destinazione, "PatchSmith-rapporto-%s%s.zip" % (nome + "-" if nome else "", adesso))
    temporaneo = percorso + ".tmp"
    try:
        with zipfile.ZipFile(temporaneo, "w", zipfile.ZIP_DEFLATED) as z:
            for nome_zip, testo in sorted(testi.items()):
                z.writestr(nome_zip, pulisci(testo, segreti).encode("utf-8"))
        os.replace(temporaneo, percorso)
    except BaseException:
        if os.path.exists(temporaneo):
            os.remove(temporaneo)
        raise
    return percorso
