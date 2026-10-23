"""Automatic procedure: from the game folder to the applied translation, with no manual steps.

Used by the graphical interface and by the `tn auto` command.
"""
import collections
import datetime
import glob
import json
import os
import re
import shutil
import struct

from . import (adapter_gamemaker, adapter_unity, adapter_unreal, adapter_xna, gamemaker, language as lingua,
               official_language as lingua_ufficiale)
from . import (adapter_csv, adapter_godot, adapter_unity_loc, adapter_web_live as adapter_web_vivo, adapter_webjson,
               configurator as configuratore, shared_file as file_condiviso, interruption as interruzione,
               names as nomi)
from . import language_unreal as lingua_unreal, detection as rilevamento, scan as scansione, system as sistema
from .backend import crea_backend
from .outcome import DAL_VIVO, imposta as _segna
from .project import SUFFISSO_BACKUP, Progetto, cartella_dati, leggi_info, percorso_memoria, ripristina_file
from .project import trova_info
from .text import DETTAGLIO, e_testo_leggibile, slug

CARTELLA_PROFILI = None  # None = profiles/ among the user's data (progetto.cartella_dati); the tests change it
MOTORI_WEB = {"Web", "Electron", "NWJS"}
# Folder names too generic to serve as the game profile's name: the parent folder is used.
NOMI_GENERICI = {"game", "games", "gioco", "bin", "bin64", "win64", "win32", "x64", "x86", "app", "data",
                 "release", "build", "windows", "windowsnoeditor", "binaries", "resources", "content"}
# reasons of the outcomes (outcome.py), in words for the player
MOTIVO_ANTICHEAT = ("Il gioco ha un sistema anti-cheat: aggiungere o cambiare i suoi file potrebbe far bannare "
                    "l'account.")
MOTIVO_IL2CPP = ("E' un gioco Unity compilato in IL2CPP che non usa le tabelle dei testi di Unity Localization: per "
                 "ora PatchSmith sa tradurre solo i giochi Unity Mono e quelli con queste tabelle.")
MOTIVO_CIFRATO = ("Il gioco tiene testi e codice in un archivio cifrato (%s), che si apre solo mentre il gioco gira. "
                  "PatchSmith non forza le protezioni messe dagli sviluppatori, quindi questo gioco non si puo' "
                  "tradurre.")
MOTIVO_WEB_VIVO = ("I testi del gioco sono cifrati: li traduce PatchSmith mentre giochi, nella pagina del gioco, "
                   "senza aprire il file cifrato. La prima volta una frase compare in inglese per un attimo, poi in "
                   "italiano; dalla volta dopo e' subito tradotta.")
MOTIVO_CRC = "Il gioco controlla l'impronta (CRC) dei file con i testi: un file modificato non verrebbe caricato."
MOTIVO_CATALOGO_BINARIO = ("Il gioco elenca i suoi file con i testi in un catalogo binario (catalog.bin, dei giochi "
                           "Unity piu' recenti) che PatchSmith non sa ancora leggere: non posso sapere se un file dei "
                           "testi modificato verrebbe caricato, quindi non modifico nulla.")
MOTIVO_TABELLE_SENZA_ORIGINE = ("Il gioco ha le tabelle dei testi di Unity Localization, ma non nella lingua di "
                                "partenza (%s): le sue lingue sono %s. Scegline una in \"Da:\" e riprova.")
# profilo["dal_vivo"] of Unity games: "servizio" = PatchSmith translates the new sentences while the game is played
# (the plugin starts servizio_xunity; it is the normal choice); "google" = XUnity asks Google by itself (only written
# by hand)
DAL_VIVO_SCELTE = ("servizio", "google", "nessuno")  # nessuno: chosen at the end of "Analyze and translate"


def _normalizza(percorso):
    return os.path.normcase(os.path.abspath(percorso)).rstrip("\\/")


def _dentro(figlio, padre):
    figlio, padre = _normalizza(figlio), _normalizza(padre)
    return figlio == padre or figlio.startswith(padre + os.sep)


def cartella_profili():
    return CARTELLA_PROFILI or cartella_dati("profiles")


def elenco_profili():
    for percorso in sorted(glob.glob(os.path.join(glob.escape(cartella_profili()), "*.json"))):
        # <nome>.stato.json: fingerprints of versions 0.1-0.2; <nome>.controllo.json: the after-play check
        if percorso.endswith((".stato.json", ".controllo.json")):
            continue
        try:
            with open(percorso, encoding="utf-8") as f:
                dati = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(dati, dict):  # a damaged file (e.g. a list) is not a game profile: skipped like an unreadable one
            yield percorso, dati


def trova_profilo(cartella):
    """Game profile of the same game: same folder, or a folder that contains it or is contained in it."""
    esatti, vicini = [], []
    for percorso, profilo in elenco_profili():
        registrata = profilo.get("cartella_gioco", "")
        if not registrata:
            continue
        if _normalizza(registrata) == _normalizza(cartella):
            esatti.append(percorso)
        elif _dentro(registrata, cartella) or _dentro(cartella, registrata):
            vicini.append(percorso)
    if esatti:
        return esatti[0]
    return vicini[0] if len(vicini) == 1 else None


def _nome_base(cartella):
    """The game profile's name for the folder, without the number that tells it apart from existing ones ('-2')."""
    parti = [p for p in os.path.abspath(cartella).replace("\\", "/").split("/") if p]
    nome = parti[-1] if parti else "gioco"
    for p in reversed(parti):
        if p.lower() not in NOMI_GENERICI:
            nome = p
            break
    return slug(nome)


def nome_per_cartella(cartella):
    base = _nome_base(cartella)
    candidato, n = base, 2
    esistenti = {os.path.splitext(os.path.basename(p))[0] for p, _ in elenco_profili()}
    while candidato in esistenti:
        candidato, n = "%s-%d" % (base, n), n + 1
    return candidato


# --- the same game in another folder (copied, moved, reinstalled) ----------------------------------------------

# executables that do not tell which game it is (programs common to many games)
_ESEGUIBILI_COMUNI = {"nw.exe", "electron.exe", "node.exe", "python.exe", "pythonw.exe", "notepad.exe", "elevate.exe",
                      "dotnet.exe", "7z.exe", "7za.exe", "qtwebengineprocess.exe", "cefsharp.browsersubprocess.exe",
                      "steamerrorreporter.exe", "steamerrorreporter64.exe",
                      # .NET published with its runtime (XNA/FNA/MonoGame games), Ren'Py, DOSBox/ScummVM, Squirrel
                      "createdump.exe", "zsync.exe", "zsyncmake.exe", "dosbox.exe", "scummvm.exe", "update.exe",
                      "squirrel.exe"}


def identita(cartella, steam=True):
    """What recognizes the same game even in another folder (read-only, fast): {'exe': [lowercase names of the game's
    executables, down to two subfolders], 'unity': 'azienda/prodotto' (Unity's app.info), 'steam': appid (Steam
    manifest, if the game is installed from Steam; with steam=False it is not looked for), 'nome': the folder name as
    for the game profile, without the '-2'}. It stays in the game profile (profilo['identita']): a moved game is
    recognized even when the previous folder is no longer there."""
    from . import language_unity as lingua_unity
    from . import steam as modulo_steam
    uscita = {"nome": _nome_base(cartella)}
    try:
        exe = sorted({e.rsplit("/", 1)[-1] for e in modulo_steam._eseguibili_cartella(cartella)} - _ESEGUIBILI_COMUNI)
    except OSError:
        exe = []
    if exe:
        uscita["exe"] = exe[:20]
    try:
        dati = lingua_unity.cartella_dati(cartella)
        azienda, prodotto = lingua_unity.azienda_prodotto(dati) if dati else (None, None)
    except (OSError, ValueError):
        azienda = prodotto = None
    if prodotto:
        uscita["unity"] = "%s/%s" % (azienda or "", prodotto)
    if steam:
        try:
            installato = modulo_steam.gioco_installato(cartella)
        except Exception:  # noqa: BLE001 - Steam missing or unreadable: do without it
            installato = None
        if installato and installato.get("appid"):
            uscita["steam"] = str(installato["appid"])
    return uscita


def _senza_numero(nome):
    return re.sub(r"-\d+$", "", nome or "")


def stesso_gioco(a, b):
    """True if the two identities (see identita) belong to the same game. The surest data both have decides: the Unity
    product, then the Steam appid, then an executable with the same name; without any of these, the folder name. The
    '-2' is removed only from a game profile's name (da_profilo: identity of an old game profile, of which only the name
    is known): 'gioco-2' as a folder is usually the sequel, not a copy."""
    for chiave in ("unity", "steam"):
        if a.get(chiave) and b.get(chiave):
            return str(a[chiave]).lower() == str(b[chiave]).lower()
    if a.get("exe") and b.get("exe"):
        return bool(set(a["exe"]) & set(b["exe"]))
    x, y = a.get("nome") or "", b.get("nome") or ""
    if not x or not y or "gioco" in (x, y):
        return False
    return (x == y or (bool(b.get("da_profilo")) and _senza_numero(y) == x)
            or (bool(a.get("da_profilo")) and _senza_numero(x) == y))


def _quante_voci(percorso):
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        return 0
    return len(dati) if isinstance(dati, dict) else 0


def stessi_giochi(cartella, destinazione=None, prova=False):
    """The game profiles of the same game (see stesso_gioco) in another folder, with translations saved in the
    'destinazione' language, when this folder does not have a game profile yet: [{'profilo', 'nome', 'cartella',
    'testi', 'spostato'}], first the one with the most recently used translation memory. 'spostato': that game
    profile's folder is no longer there (game moved or reinstalled elsewhere). prova=True: the translation memories of
    the test translator ('finto' backend)."""
    if trova_profilo(cartella):
        return []
    mia, uscita = None, []
    for percorso, profilo in elenco_profili():
        registrata = profilo.get("cartella_gioco")
        if not registrata or not profilo.get("nome") or not profilo.get("lingua_destinazione"):
            continue
        if _dentro(registrata, cartella) or _dentro(cartella, registrata):
            continue
        if destinazione and not lingua.stessa_lingua(profilo["lingua_destinazione"], destinazione):
            continue
        memoria = percorso_memoria(profilo, prova)
        testi = _quante_voci(memoria)
        if not testi:
            continue
        spostato = not os.path.isdir(registrata)
        sua = profilo.get("identita") if isinstance(profilo.get("identita"), dict) else None
        if sua is None:  # game profile from before this version: from its folder, if still there, or from the name
            sua = identita(registrata, steam=False) if not spostato else {"nome": profilo["nome"], "da_profilo": True}
        if mia is None:
            mia = identita(cartella)
        if not stesso_gioco(mia, sua):
            continue
        try:
            data = os.path.getmtime(memoria)
        except OSError:
            data = 0
        uscita.append((data, {"profilo": percorso, "nome": profilo["nome"], "cartella": registrata, "testi": testi,
                              "spostato": spostato}))
    return [v for _, v in sorted(uscita, key=lambda x: -x[0])]


def _sposta_profilo(da_profilo, cartella, log):
    """The game profile's game was moved (its folder is no longer there): the game profile moves to the new folder,
    with the same translation memory. True if it did so."""
    try:
        profilo = _leggi_profilo(da_profilo)
    except (OSError, ValueError):
        return False
    vecchia = profilo.get("cartella_gioco") if isinstance(profilo, dict) else None
    if not vecchia or os.path.isdir(vecchia):
        return False
    profilo["cartella_gioco"] = os.path.abspath(cartella).replace(os.sep, "/")
    profilo["cartella_precedente"] = vecchia
    scansione.salva_json(da_profilo, profilo)
    log("Stesso gioco di \"%s\", che era in %s: uso il suo profilo e le sue traduzioni in questa cartella."
        % (profilo.get("nome"), vecchia))
    return True


def _copia_traduzioni(da_profilo, percorso, log):
    """The saved translations of the same game in another folder (translation memory and machine translations, the
    test ones too, and the copy of the XUnity cache) become those of this game profile, if it has none yet; the game's
    names removed or added by hand (non_tradurre) come along too. Returns the entries copied."""
    if _normalizza(da_profilo) == _normalizza(percorso):
        return 0
    try:
        vecchio, nuovo = _leggi_profilo(da_profilo), _leggi_profilo(percorso)
        if not lingua.stessa_lingua(vecchio["lingua_destinazione"], nuovo["lingua_destinazione"]):
            return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    copiate = 0
    try:
        for prova in (False, True):
            sorgente, destinazione = percorso_memoria(vecchio, prova), percorso_memoria(nuovo, prova)
            if not os.path.exists(sorgente) or os.path.exists(destinazione):
                continue
            for coda in (".json", ".automatiche.json"):
                da_file, a_file = sorgente[:-len(".json")] + coda, destinazione[:-len(".json")] + coda
                if os.path.exists(da_file) and not os.path.exists(a_file):
                    # with the lock and on a temporary name: the other copy's translation memory may be in use
                    # (the open game is updating it) and no half-written file must be left here
                    dati = file_condiviso.leggi_json(da_file)
                    if isinstance(dati, dict):
                        file_condiviso.salva_unendo(a_file, dict(dati), {})
            copiate += _quante_voci(destinazione)
        cache = adapter_unity._cartella_memoria(vecchio["nome"])
        if os.path.isdir(cache) and not os.path.exists(adapter_unity._cartella_memoria(nuovo["nome"])):
            shutil.copytree(cache, adapter_unity._cartella_memoria(nuovo["nome"]))
    except (OSError, ValueError) as e:  # (disk full, damaged file): translate what is missing, as without it
        log("  ! Non sono riuscito a copiare tutte le traduzioni di \"%s\" (%s): traduco quello che manca."
            % (vecchio["nome"], e))
    # the names not to translate: those chosen for the other copy (removed or added by hand) apply here too
    visti = [n for n in vecchio.get("nomi_trovati") or [] if isinstance(n, str)]
    if vecchio.get("non_tradurre") is not None:
        nuovo["non_tradurre"] = list(dict.fromkeys(
            [n for n in vecchio.get("non_tradurre") or [] if isinstance(n, str)] +
            [n for n in nuovo.get("non_tradurre") or [] if isinstance(n, str) and n not in visti]))
        nuovo["nomi_trovati"] = list(dict.fromkeys(visti + [n for n in nuovo.get("nomi_trovati") or []
                                                            if isinstance(n, str)]))
    nuovo["traduzioni_da"] = vecchio["nome"]
    scansione.salva_json(percorso, nuovo)
    if copiate:
        log("Riuso le traduzioni di \"%s\" (stesso gioco in %s): %d testi gia' tradotti."
            % (vecchio["nome"], vecchio.get("cartella_gioco"), copiate))
    else:
        log("  (le traduzioni di \"%s\" non servono: questa cartella ha gia' le sue)" % vecchio["nome"])
    return copiate


def gioco_supportato(esito):
    """(supportato, web).

    web: Electron/NW.js, a web page with no other engines, or RPG Maker MV/MZ (they have an index.html).
    supportato: translatable automatically. RPG Maker MV/MZ stays experimental (confirmation needed):
    its events mix dialogues and plugin commands (often Japanese words used as
    identifiers), which the generic rules cannot tell apart.
    """
    motori = {r["nome"] for r in esito["risultati"] if r["sezione"] == "Engine"}
    contenitori = {r["nome"] for r in esito["risultati"] if r["sezione"] == "Container"}
    sdk = {r["nome"] for r in esito["risultati"] if r["sezione"] == "SDK"}
    rpg_web = "RPG Maker MV/MZ" in esito["framework_web"] or "RPGMaker" in motori
    web = bool(contenitori & {"Electron"} or sdk & {"NWJS"} or rpg_web or motori == {"Web"})
    return web and not rpg_web, web


def motore_unity(esito):
    """'mono', 'il2cpp' or None if the game is not Unity."""
    if esito.get("motore_principale") != "Unity":
        return None
    sdk = {r["nome"] for r in esito["risultati"] if r["sezione"] == "SDK"}
    return "il2cpp" if "UnityIL2CPP" in sdk else "mono"


def motore_unreal(esito):
    return esito.get("motore_principale") == "Unreal"


def _prepara_unreal(cartella, esistente, origine, destinazione, esito, log, backend="google"):
    """Game profile for an Unreal game: the .locres files are translated and a patch pak is added."""
    if ha_anticheat(esito):
        log("Questo gioco ha un anti-cheat: non aggiungo pak modificati (rischio di ban).")
        return None, _segna(esito, "ANTICHEAT", MOTIVO_ANTICHEAT, motore="unreal")
    info = adapter_unreal.analizza(cartella)
    if not info["paks"]:
        log("Non trovo archivi .pak del gioco.")
        return None, _segna(esito, "NON_SUPPORTATO", "Non trovo gli archivi .pak con i dati di questo gioco Unreal.",
                            motore="unreal", azioni=["crea_rapporto"])
    for problema in info["problemi"]:
        log("  ! " + problema)
    if not info["versioni"]:
        return None, _segna(esito, "NON_SUPPORTATO", "Gli archivi dei dati del gioco (.pak) sono in un formato che "
                            "PatchSmith non sa ancora leggere (Unreal 5, IoStore o archivi cifrati).", motore="unreal",
                            azioni=["crea_rapporto"])
    # the localized texts (.locres) are looked for before creating the game profile: a game without them does not
    # leave an empty game profile, and an unreadable pak does not stop the search in the others
    saltati = []
    sorgenti = adapter_unreal.trova_locres(cartella, saltati)
    if saltati:
        log("  ! %d archivi non letti (li salto): %s" % (len(saltati), "; ".join(saltati[:3])))
    if not sorgenti:
        log("Nessun file di localizzazione (.locres) negli archivi del gioco che PatchSmith sa leggere.")
        if lingua_unreal.lingue_del_gioco(cartella)[0] or saltati:
            # the .locres files are there (outside the archives) or might be in the archives that cannot be read
            motivo = ("I testi del gioco (.locres) sono in file che PatchSmith non sa ancora tradurre (fuori dagli "
                      "archivi .pak, o in archivi di formato piu' nuovo o cifrati).")
        else:
            motivo = ("Il gioco non ha testi localizzati (file .locres): i suoi testi sono dentro gli altri dati del "
                      "gioco, che oggi PatchSmith non sa tradurre.")
        return None, _segna(esito, "NON_SUPPORTATO", motivo, motore="unreal", azioni=["crea_rapporto"])
    if esistente:
        profilo = _leggi_profilo(esistente)
        profilo.update({"motore": "unreal", "lingua_origine": origine, "lingua_destinazione": destinazione,
                        "backend": backend})
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"),
                   "motore": "unreal", "lingua_origine": origine, "lingua_destinazione": destinazione,
                   "backend": backend, "file": []}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, profilo)
    log("== 2/3 Unreal: traduco i file di localizzazione (.locres) e li metto in un pak di patch aggiuntivo")
    return percorso, esito


def unreal_completo(percorso_profilo, log=print, rete=True, backend="google", esito=None):
    """Translates the .locres files (rete=True: new texts too; False: only from the translation memory) and writes the
    pak. backend="finto": test without network, with the separate test translation memory. The outcome goes into
    esito["esito"]."""
    profilo = _leggi_profilo(percorso_profilo)
    cartella, a = profilo["cartella_gioco"], profilo["lingua_destinazione"]
    sorgenti, testi, cultura = adapter_unreal.testi_da_tradurre(cartella, log)
    da = profilo.get("lingua_origine") or lingua.AUTO
    if da == lingua.AUTO:
        base = cultura.split("-")[0]
        da = base if base in lingua.NOMI else cultura
        log("  Lingua originale del gioco (dal file .locmeta): %s" % lingua.etichetta(da))
    if da.split("-")[0] == a.split("-")[0]:
        log("  Il gioco e' gia' in %s: niente da tradurre." % lingua.etichetta(a))
        _segna(esito, "NULLA_DA_FARE", _motivo_gia_in(a), motore="unreal")
        return False
    progetto = Progetto(percorso_profilo, log, prova=(backend == "finto"))
    falliti = progetto.traduci_testi(testi, crea_backend(backend, log), da, a) if rete else 0
    interruzione.controlla()
    respinte = {}
    n = adapter_unreal.installa(cartella, a, profilo["nome"], progetto.memoria, log, respinte=respinte)
    # translations with characters the game's fonts lack: into problemi.json as for the other engines (those written
    # by PatchSmith are redone at the next translation without those characters)
    progetto._respinte = {t: {"motivo": "caratteri che i font del gioco non sanno disegnare: %s" % " ".join(m),
                              "file": adapter_unreal.nome_pak(a), "non_usare": m} for t, m in respinte.items()}
    progetto._applicati = {t for t in testi if isinstance(progetto.memoria.get(t), str) and t not in respinte}
    progetto._registra_applica()
    _esito_scrittura(esito, progetto, testi, bool(n), "unreal")
    return bool(n) and not falliti


def motore_xna(esito):
    return esito.get("motore_principale") in ("XNA", "FNA", "MonoGame")


def _prepara_xna(cartella, esistente, origine, destinazione, esito, log, backend="google", traduci_comunque=False):
    """Game profile for an XNA/FNA/MonoGame game with localized .NET resources. None if it has none at all."""
    if ha_anticheat(esito):
        log("Questo gioco ha un anti-cheat: non aggiungo file (rischio di ban).")
        return None, _segna(esito, "ANTICHEAT", MOTIVO_ANTICHEAT, motore="xna")
    info = adapter_xna.analizza(cartella)
    for problema in info["problemi"]:
        log("  ! " + problema)
    if not info["insiemi"]:
        if info["problemi"]:  # there are localized resources, but not usable (e.g. signed assemblies)
            return None, _segna(esito, "NON_SUPPORTATO", "Le risorse di testo del gioco sono firmate (assembly con "
                                "nome sicuro): PatchSmith non sa ancora tradurle.", motore="xna",
                                azioni=["crea_rapporto"])
        log("  Nessuna risorsa .NET localizzata (.resources con satelliti): provo la ricerca generica dei testi.")
        return None
    if esistente:
        profilo = _leggi_profilo(esistente)
        profilo.update({"motore": "xna", "lingua_origine": origine, "lingua_destinazione": destinazione,
                        "backend": backend, "traduci_comunque": traduci_comunque})
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"),
                   "motore": "xna", "lingua_origine": origine, "lingua_destinazione": destinazione,
                   "backend": backend, "file": [], "traduci_comunque": traduci_comunque}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, profilo)
    log("== 2/3 .NET: traduco le risorse di testo e le aggiungo come assembly satellite (file del gioco intatti)")
    return percorso, esito


def xna_completo(percorso_profilo, log=print, rete=True, backend="google", esito=None):
    """Translates the .NET resources (rete=True: new texts too; False: only from the translation memory) and writes
    the satellites. The outcome goes into esito["esito"]."""
    profilo = _leggi_profilo(percorso_profilo)
    cartella, a = profilo["cartella_gioco"], profilo["lingua_destinazione"]
    insiemi, testi = adapter_xna.testi_da_tradurre(cartella, log)
    da = profilo.get("lingua_origine") or lingua.AUTO
    if da == lingua.AUTO:
        da, motivo = lingua.rileva(testi)
        log("  Lingua originale del gioco: %s (%s)" % (lingua.etichetta(da), motivo))
    if da.split("-")[0] == a.split("-")[0]:
        log("  Il gioco e' gia' in %s: niente da tradurre." % lingua.etichetta(a))
        _segna(esito, "NULLA_DA_FARE", _motivo_gia_in(a), motore="xna")
        return False
    if not profilo.get("traduci_comunque") and a.split("-")[0].lower() in adapter_xna.lingue_presenti(insiemi):
        log("  Il gioco ha gia' la traduzione ufficiale in %s: sceglila nel menu delle lingue del gioco."
            % lingua.etichetta(a))
        _segna(esito, "UFFICIALE", _motivo_ufficiale(a), motore="xna", azioni=["scegli_lingua", "traduci_comunque"])
        return False
    progetto = Progetto(percorso_profilo, log, prova=(backend == "finto"))
    falliti = progetto.traduci_testi(testi, crea_backend(backend, log), da, a) if rete else 0
    interruzione.controlla()
    n = adapter_xna.installa(cartella, da, a, profilo["nome"], progetto.memoria, log)
    _esito_scrittura(esito, progetto, testi, bool(n), "xna")
    return bool(n) and not falliti


def motore_godot(esito):
    return esito.get("motore_principale") == "Godot"


def _prepara_godot(cartella, esistente, origine, destinazione, esito, log, backend="google"):
    """Game profile of a Godot 4 game with Godot's translation tables (see adapter_godot). None, None if the game has
    no tables (the generic route of text files is tried)."""
    trovato = adapter_godot.trova_pck(cartella)
    if not trovato:
        return None, None
    try:
        pck = adapter_godot.Pck(trovato[0])
        tutte = adapter_godot.tabelle(pck)
    except (adapter_godot.ErroreGodot, OSError, ValueError) as e:
        log("  Godot: non riesco a leggere il pacchetto dei dati (%s)" % e)
        motivo = ("Il pacchetto dei dati del gioco (Godot) e' cifrato: PatchSmith non forza le protezioni degli "
                  "sviluppatori." if "cifrat" in str(e) else "Non riesco a leggere il pacchetto dei dati del gioco "
                  "(Godot): %s." % e)
        return None, _segna(esito, "NON_SUPPORTATO", motivo, motore="godot", azioni=["crea_rapporto"])
    log(DETTAGLIO + "  Godot %s, pacchetto %s, tabelle delle traduzioni: %s" % (
        pck.versione(), os.path.relpath(trovato[0], cartella), ", ".join(sorted(tutte)) or "nessuna"))
    if not tutte:
        return None, None
    if any(lingua.stessa_lingua(c, destinazione) for c in tutte):
        log("Il gioco ha gia' la traduzione in %s: sceglila nel menu delle lingue del gioco."
            % lingua.etichetta(destinazione))
        return None, _segna(esito, "UFFICIALE", _motivo_ufficiale(destinazione), motore="godot",
                            azioni=["scegli_lingua"])
    sorgente = origine if origine and origine != lingua.AUTO else "en"
    codice = lingua.migliore(tutte, sorgente)
    if codice is None:
        return None, _segna(esito, "NON_SUPPORTATO", MOTIVO_TABELLE_SENZA_ORIGINE % (
            lingua.etichetta(sorgente), ", ".join(_nome_lingua(c) for c in sorted(tutte))), motore="godot",
            azioni=["crea_rapporto"])
    voce = {"pck": os.path.relpath(trovato[0], cartella).replace(os.sep, "/"), "tabella": tutte[codice],
            "lingua": codice}
    if esistente:
        profilo = _leggi_profilo(esistente)
        profilo.update({"motore": "godot", "godot": voce, "file": [], "lingua_origine": origine,
                        "lingua_destinazione": destinazione, "backend": backend,
                        "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/")})
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"), "motore": "godot",
                   "godot": voce, "lingua_origine": origine, "lingua_destinazione": destinazione,
                   "backend": backend, "file": []}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, profilo)
    log("== 2/3 Godot: tabelle dei testi in %d lingue (%s); traduco quella in %s, senza toccare i file del gioco"
        % (len(tutte), ", ".join(sorted(tutte)), lingua.etichetta(codice)))
    return percorso, esito


def godot_completo(percorso_profilo, log=print, rete=True, backend="google", esito=None):
    """Translates the table of the source language (rete=True) and puts override.cfg, the script and the translations
    next to the executable (see adapter_godot). rete=False: only the translations already in memory (Reapply)."""
    profilo = _leggi_profilo(percorso_profilo)
    cartella, a, voce = profilo["cartella_gioco"], profilo["lingua_destinazione"], profilo["godot"]
    if sistema.gioco_aperto(cartella):
        log("Il gioco e' aperto: chiudilo e riprova.")
        _segna(esito, "GIOCO_APERTO", "Il gioco e' aperto.", motore="godot", azioni=["chiudi_gioco"])
        return False
    pck_path = os.path.join(cartella, *voce["pck"].split("/"))
    _lingua, testi = adapter_godot.testi_tabella(adapter_godot.Pck(pck_path).leggi(voce["tabella"]))
    testi = list(dict.fromkeys(t for t in testi if t.strip()))
    log("  Testi nella tabella del gioco: %d" % len(testi))
    da = voce["lingua"]
    prova = backend == "finto"
    progetto = Progetto(percorso_profilo, log, prova=prova)
    falliti = 0
    if rete:
        traduttore = crea_backend(backend, log)
        if not prova and profilo.get("backend") != backend:
            profilo["backend"] = backend
            scansione.salva_json(percorso_profilo, profilo)
        falliti = progetto.traduci_testi(testi, traduttore, da, a)
    traduzioni = {t: progetto.memoria[t] for t in testi if isinstance(progetto.memoria.get(t), str)}
    if sistema.gioco_aperto(cartella):
        log("  ! Il gioco e' stato aperto nel frattempo: chiudilo e premi 'Riapplica' (le traduzioni sono gia' salvate).")
        _segna(esito, "GIOCO_APERTO", "Il gioco e' stato aperto mentre lavoravo: le traduzioni sono salvate ma non "
               "ancora nel gioco.", motore="godot", azioni=["chiudi_gioco", "riapplica"])
        return False
    adapter_godot.installa(os.path.dirname(pck_path), da, traduzioni, a, log)
    _esito_scrittura(esito, progetto, testi, True, "godot",
                     motivo="Nel gioco lascia selezionata la lingua %s: i testi compaiono in %s." % (
                         lingua.etichetta(da), lingua.etichetta(a)))
    if falliti:
        _dopo_i_tentativi(esito, log)
    return not falliti


def motore_gamemaker(esito):
    return esito.get("motore_principale") == "GameMaker"


def _prepara_gamemaker(cartella, esistente, origine, destinazione, esito, log, backend="google",
                       traduci_comunque=False):
    """Game profile for a GameMaker game with external localization files. None if it has none."""
    radici = gamemaker.radici(cartella)
    if len(radici) > 1:
        log("Nella cartella ci sono %d giochi GameMaker: scegli la cartella di uno solo." % len(radici))
        return None, _segna(esito, "CARTELLA_SBAGLIATA", "Nella cartella ci sono %d giochi GameMaker: scegli la "
                            "cartella di uno solo." % len(radici), motore="gamemaker")
    if radici and os.path.normcase(radici[0]) != os.path.normcase(os.path.abspath(cartella)):
        log("  Cartella del gioco: %s" % radici[0])
        cartella = radici[0]
        esistente = esistente or trova_profilo(cartella)
    trovati = adapter_gamemaker.file_localizzazione(cartella)
    sorgenti, lingue, base = adapter_gamemaker.scegli_sorgenti(trovati, origine, cartella)
    formato = "righe"
    if not sorgenti:  # a JSON file for each language (e.g. Forager: local/english.json, local/french.json...)
        trovati_json = adapter_gamemaker.file_json_lingue(cartella, log)
        sorgenti_json, lingue_json, base_json = adapter_gamemaker.scegli_sorgenti(trovati_json, origine, cartella)
        if sorgenti_json:
            trovati, sorgenti, lingue, base, formato = trovati_json, sorgenti_json, lingue_json, base_json, "json"
    tabelle = {}
    if not sorgenti:  # CSV tables with one column for each language (e.g. translations.csv: id,english,french...)
        tabelle = {r: f for r, f in adapter_csv.tabelle(cartella).items() if f.sorgente(origine)[0] is not None}
        if tabelle:
            sorgenti, formato = sorted(tabelle), "csv"
            trovati = {}
            base = tabelle[sorgenti[0]].sorgente(origine)[1]
            lingue = set()
            for f in tabelle.values():
                lingue |= {c.split("-")[0].lower() for c in f.lingue_piene(f.sorgente(origine)[0])}
            log("  Tabelle dei testi con una colonna per lingua: %s" % ", ".join(sorgenti))
    if not sorgenti:
        log("  Nessun file di localizzazione riconosciuto: provo la ricerca generica dei testi." if not trovati else
            "  Ci sono file di localizzazione, ma non riconosco quelli della lingua originale: provo la ricerca generica.")
        return None
    if destinazione.split("-")[0].lower() in lingue and not traduci_comunque:
        log("Il gioco ha gia' la traduzione in %s: sceglila nel menu delle lingue del gioco."
            % lingua.etichetta(destinazione))
        return None, _segna(esito, "UFFICIALE", _motivo_ufficiale(destinazione), motore="gamemaker",
                            azioni=["scegli_lingua", "traduci_comunque"])
    if ha_anticheat(esito):
        log("Questo gioco ha un anti-cheat: non modifico i suoi file (rischio di ban).")
        return None, _segna(esito, "ANTICHEAT", MOTIVO_ANTICHEAT, motore="gamemaker")
    log("  File di testo della lingua originale: %d (altre lingue nel gioco: %s)"
        % (len(sorgenti), ", ".join(sorted(lingue - {base})) or "nessuna"))
    if base and base != "en" and "en" in lingue:
        log("  ! Traduco i file in %s: nel gioco dovrai selezionare quella lingua, non l'inglese."
            % lingua.etichetta(base))
    if esistente:  # files translated by a previous game profile (e.g. experimental) that must not be translated now
        _rimetti_esclusi(cartella, [v.get("percorso") for v in _leggi_profilo(esistente).get("file", [])
                                    if v.get("percorso") and v.get("percorso") not in sorgenti], log)
    if formato == "righe":
        voci = [{"percorso": r, "formato": "righe"} for r in sorgenti]
    elif formato == "csv":
        voci = [{"percorso": r, "formato": "csv", "localizzazione": True,
                 "colonna": tabelle[r].intestazione[tabelle[r].sorgente(origine)[0]].strip().lstrip("\ufeff")}
                for r in sorgenti]
    else:
        voci = _voci_json(cartella, trovati, sorgenti, log, destinazione)
    ufficiali = adapter_gamemaker.lettere_ufficiali(cartella, trovati, sorgenti) if formato != "csv" else \
        _lettere_tabelle(tabelle, origine)
    if esistente:
        profilo = _leggi_profilo(esistente)
        profilo.update({"motore": "gamemaker", "lingua_origine": origine, "lingua_destinazione": destinazione,
                        "backend": backend, "file": voci, "lingua_file": base, "lettere_ufficiali": ufficiali,
                        "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/")})
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"),
                   "motore": "gamemaker", "lingua_origine": origine, "lingua_destinazione": destinazione,
                   "backend": backend, "file": voci, "lingua_file": base, "lettere_ufficiali": ufficiali}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, profilo)
    log("== 2/3 GameMaker: traduco i file di testo della lingua originale (backup automatico degli originali)")
    return percorso, esito


def _lettere_tabelle(tabelle, origine):
    """Accented Latin letters of the official translations in the CSV tables (the columns of the other languages)."""
    lettere = set()
    for f in tabelle.values():
        sorgente = f.sorgente(origine)[0]
        for i in f.lingue:
            if i != sorgente:
                lettere |= {c for _, t in f.testi(i) for c in t if adapter_gamemaker._latino_accentato(c)}
    return "".join(sorted(lettere))


def _voci_json(cartella, trovati, sorgenti, log, destinazione=None):
    """Game profile entries for the original language's JSON localization files, with what can be learned from the
    game and from its official translations in the same folder:
    - writing conventions (line breaks, paragraph start symbol) and use of capitals (lowercase games with capitals
      as font icons, e.g. Forager: 'EP', 'H');
    - the space of each text (longest line and number of lines of the versions that use the same font);
    - the official translations to show the local translator as a reference;
    - if the game loads all the files in the folder by itself (e.g. 'local/*.json'), the translation becomes a new
      language (local/italiano.json) and the original stays as it is."""
    voci = []
    leggi = adapter_gamemaker._leggi_json
    dati_win, mappa = None, None
    percorso_win = adapter_gamemaker.file_dati(cartella)
    try:
        if percorso_win:
            dati_win = gamemaker.leggi(percorso_win)
            mappe = gamemaker.mappe_font_sprite(os.path.dirname(percorso_win), dati_win)
            mappa = max((m for _, m in mappe), key=len) if mappe else None
    except (gamemaker.ErroreGameMaker, OSError, ValueError, IndexError, struct.error):
        dati_win = dati_win if isinstance(dati_win, bytes) else None
    for rel in sorgenti:
        # from the original (backup), not from the file already translated by a previous version
        dati = adapter_gamemaker.leggi_originale(os.path.join(cartella, *rel.split("/")), leggi)
        altri = []  # (codice, relativo, dati) of the official translations of the same file
        for altro in trovati:
            if altro not in sorgenti and adapter_gamemaker.famiglia(altro) == adapter_gamemaker.famiglia(rel):
                try:
                    altri.append((trovati[altro], altro,
                                  adapter_gamemaker.leggi_originale(os.path.join(cartella, *altro.split("/")), leggi)))
                except (OSError, ValueError):
                    continue
        # the texts by key (also inside sections: 'menu._play'), here and in the official translations
        piatti, altri_piatti = adapter_gamemaker.piatto(dati), [adapter_gamemaker.piatto(d) for _, _, d in altri]
        convenzioni = adapter_gamemaker.markup(piatti, altri_piatti)
        testi = [v for v in piatti.values() if adapter_gamemaker.testo_localizzazione(v)]
        stile = adapter_gamemaker.stile_maiuscole(testi)
        codici = adapter_gamemaker.codici_maiuscoli(testi, mappa, convenzioni["punto_elenco"]) \
            if stile == "minuscolo" else []
        dedicati = adapter_gamemaker.font_dedicati(dati_win, [c for c, _, _ in altri]) if dati_win else set()
        stesso_font = [p for (c, _, _), p in zip(altri, altri_piatti) if c.split("-")[0] not in dedicati]
        voce = {"percorso": rel, "formato": "json", "localizzazione": True,
                "includi": adapter_gamemaker.schemi_json(dati), "escludi": [],
                "a_capo": convenzioni["a_capo"], "punto_elenco": convenzioni["punto_elenco"],
                "maiuscole": stile, "codici": codici, "riferimenti": {c: r for c, r, _ in altri},
                "limiti": adapter_gamemaker.limiti_righe(piatti, stesso_font, convenzioni["a_capo"])
                if convenzioni["a_capo"] else {}}
        cartella_rel = os.path.dirname(rel)
        rispetto_al_gioco = os.path.relpath(os.path.join(cartella, *cartella_rel.split("/")),
                                            os.path.dirname(percorso_win)).replace(os.sep, "/") if percorso_win else ""
        if destinazione and cartella_rel and dati_win and not rispetto_al_gioco.startswith("..") and \
                gamemaker.cartella_maschera(os.path.dirname(percorso_win), rispetto_al_gioco,
                                            os.path.splitext(rel)[1], dati_win):
            nome = adapter_gamemaker.nome_lingua_nuova(destinazione, os.path.join(cartella, *cartella_rel.split("/")),
                                                       os.path.splitext(rel)[1])
            if nome:
                voce["lingua_nuova"] = cartella_rel + "/" + nome
        voci.append(voce)
        dette = []
        if convenzioni["a_capo"]:
            dette.append("'%s' = a capo" % convenzioni["a_capo"])
        if convenzioni["punto_elenco"]:
            dette.append("'%s' = simbolo a inizio paragrafo" % convenzioni["punto_elenco"])
        if dette:
            log("  Convenzioni del gioco in %s: %s (rispettate nella traduzione)" % (rel, ", ".join(dette)))
        if stile == "minuscolo":
            log("  Il gioco scrive in minuscolo%s: la traduzione fa lo stesso" % (
                " e usa le maiuscole come icone (%s, protette nella traduzione)" % ", ".join(codici) if codici else ""))
        if mappa:
            log("  Font a immagini del gioco: %d caratteri (le lettere che non ha vengono adattate)" % len(mappa))
        if voce.get("lingua_nuova"):
            log("  Il gioco carica da solo le lingue in %s/: la traduzione diventa una lingua nuova (%s), "
                "l'originale resta com'e'" % (cartella_rel, voce["lingua_nuova"]))
    return voci


def lingue_della_cartella(cartella, relativo):
    """The names (without extension) of the language files of the same family as 'relativo' (e.g. english,
    french...): they are the values the game saves as the chosen language."""
    famiglia = adapter_gamemaker.famiglia(relativo)
    nomi = {os.path.splitext(os.path.basename(r))[0] for r in adapter_gamemaker.file_json_lingue(cartella)
            if adapter_gamemaker.famiglia(r) == famiglia}
    return nomi | {os.path.splitext(os.path.basename(relativo))[0]}


def _rimetti_esclusi(cartella, relativi, log):
    """Puts back the original of the files that a previous game profile had translated and that must not be
    translated now (e.g. the official translations translated by mistake by the experimental mode)."""
    for rel in relativi:
        percorso = os.path.join(cartella, *rel.split("/"))
        if not leggi_info(percorso):
            continue
        try:
            aperto = sistema.gioco_aperto(cartella)
        except sistema.ErroreSistema:
            aperto = True
        if aperto or not ripristina_file(percorso, lambda _m: None):
            log("  ! %s era stato tradotto prima ma non va tradotto: usa 'Ripristina originali' (a gioco chiuso)" % rel)
        else:
            log("  %s era stato tradotto prima ma non va tradotto: rimesso l'originale" % rel)


def _lingua_dei_testi(cartella, rel):
    """Language recognized from the texts of a JSON file (from the original), or None."""
    try:
        dati = adapter_gamemaker.leggi_originale(os.path.join(cartella, *rel.split("/")),
                                                 adapter_gamemaker._leggi_json)
    except (OSError, ValueError):
        return None
    testi = [v for _, _, v in adapter_webjson.foglie(dati) if e_testo_leggibile(v)][:3000]
    return lingua.rileva(testi)[0] if testi else None


def _solo_lingua_originale(candidati, cartella, origine, destinazione, log):
    """Among identical files in several languages (same family: local/{L}.json, lang/{L}/ui.json) keeps only those of
    the original language: the others are the game's official translations and must not be translated.
    Returns (candidates to keep, removed paths, files in the target language)."""
    famiglie = {}
    for c in candidati:
        codice = adapter_gamemaker.lingua_del_percorso(c["percorso"])
        if codice:
            famiglie.setdefault(adapter_gamemaker.famiglia(c["percorso"]), []).append((c, codice))
    base = (origine or "auto").split("-")[0].lower()
    togli, arrivo = set(), []
    for fam, elenco in sorted(famiglie.items()):
        lingue = {codice.split("-")[0] for _, codice in elenco}
        if len(lingue) < 2:
            continue
        arrivo += [c["percorso"] for c, codice in elenco if destinazione and lingua.stessa_lingua(codice, destinazione)]
        tieni = "en" if base == "auto" else base
        tenuti = [c for c, codice in elenco if codice.split("-")[0] == tieni]
        if not tenuti:
            continue
        if base == "auto":  # an English much smaller than the other files is not necessarily the original language
            piu_grande = max(c.get("stringhe_leggibili", 0) for c, _ in elenco)
            if max(c.get("stringhe_leggibili", 0) for c in tenuti) < 0.8 * piu_grande:
                continue
        tolti = []
        for c, codice in elenco:
            if codice.split("-")[0] == tieni:
                continue
            rilevata = _lingua_dei_testi(cartella, c["percorso"])
            if rilevata is None or lingua.stessa_lingua(rilevata, tieni):
                continue  # the texts are not in another language: it is not a translation, it stays
            tolti.append(c["percorso"])
        if tolti:
            togli.update(tolti)
            log("  File per lingua (%s): traduco solo quelli in %s; gli altri %d sono le traduzioni ufficiali del "
                "gioco" % (fam, lingua.etichetta(tieni), len(tolti)))
    return [c for c in candidati if c["percorso"] not in togli], togli, arrivo


def ha_anticheat(esito):
    return any(r["sezione"] == "AntiCheat" for r in esito["risultati"])


def _leggi_profilo(percorso):
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def _nome_lingua(codice):
    """'Francese (fr)' for a code of the text tables ('fr', 'zh-cn', 'pt-br'); the code itself if unknown."""
    noto = next((c for c in lingua.NOMI if c.lower() == (codice or "").lower()), None)
    return lingua.etichetta(noto or lingua.migliore(lingua.NOMI, codice) or codice) if codice else ""


def _stato_crc(percorso_bundle):
    """adapter_unity_loc.crc_del_bundle, but also looking at the catalog's original: after the first translation the
    catalog.json in the game is the one written by PatchSmith (CRC already 0, "libero"), but the game profile entry must
    keep naming it. Without this, a new "Analyze and translate" would remove it from the game profile: "Reapply" after
    the game put back its files (Steam verification) would rewrite the bundle with the CRC back in the catalog, and the
    game would no longer load it."""
    stato, catalogo = adapter_unity_loc.crc_del_bundle(percorso_bundle)
    if stato == "libero" and catalogo and os.path.isfile(catalogo + SUFFISSO_BACKUP):
        if adapter_unity_loc.crc_del_bundle(percorso_bundle, leggi_da=catalogo + SUFFISSO_BACKUP)[0] == "controllato":
            stato = "controllato"
    return stato, catalogo


def _voci_tabelle(cartella, bundle, log):
    """Game profile entries to rewrite these text table bundles, or "binario" / "crc" if the Addressables catalog
    checks their fingerprint and it cannot be removed."""
    voci = []
    for rel in bundle:
        voce = {"percorso": rel, "formato": "unity-tabelle"}
        stato, catalogo = _stato_crc(os.path.join(cartella, *rel.split("/")))
        if stato == "binario":
            log("  Il gioco elenca i suoi bundle in un catalogo binario (catalog.bin) in cui non trovo con certezza %s: "
                "non modifico le tabelle dei testi." % rel.split("/")[-1])
            return "binario"
        if stato == "controllato":
            voce["catalogo"] = os.path.relpath(catalogo, cartella).replace(os.sep, "/")
            log(DETTAGLIO + "  Il catalogo del gioco controlla l'impronta (CRC) di %s: la tolgo solo per questo file "
                "(%s con backup, 'Ripristina originali' lo rimette)" % (rel.split("/")[-1], os.path.basename(catalogo)))
        elif stato == "sconosciuto" and adapter_unity_loc.crc_controllato(os.path.join(cartella, *rel.split("/"))):
            log("  Il gioco controlla l'impronta dei suoi bundle e non trovo quella di %s nel catalogo: non posso "
                "modificare le tabelle dei testi." % rel.split("/")[-1])
            return "crc"
        voci.append(voce)
    return voci


def _tabelle_mono(cartella, origine, destinazione, log):
    """Unity Mono games with Unity Localization: besides XUnity, the text tables inside their bundles are translated.
    It is needed for the texts XUnity cannot replace, like dialogues animated letter by letter (Febucci's Text
    Animator, e.g. Big Dig: XUnity sees the sentence in pieces and the game puts the original back); this way the game
    itself gives Italian to the animation. Returns the game profile entries ([] if not possible or not needed)."""
    tutte = adapter_unity_loc.bundle_tabelle(cartella)
    if not tutte or any(lingua.stessa_lingua(c, destinazione) for c in tutte):
        return []
    sorgente = origine if origine and origine != lingua.AUTO else "en"
    codice = lingua.migliore(tutte, sorgente)
    if codice is None:
        return []
    voci = _voci_tabelle(cartella, tutte[codice], log)
    if isinstance(voci, str):
        log("  Le tabelle dei testi restano in originale nei file: le traduce solo XUnity mentre giochi.")
        return []
    log("  Unity Localization: traduco anche le tabelle dei testi in %s dentro il loro bundle (backup automatico): "
        "cosi' anche i dialoghi animati compaiono in italiano" % lingua.etichetta(sorgente))
    return voci


def _prepara_unity_tabelle(cartella, esistente, origine, destinazione, log):
    """Game profile for a Unity game with Unity Localization text tables (IL2CPP too): the tables in the source
    language (with "auto", English) are translated inside their bundles, recognized by their content too (see
    adapter_unity_loc.bundle_tabelle). Returns the game profile's path, or:
    - "ufficiale": the game already has the target language;
    - "crc": the catalog checks the bundles' fingerprint and it cannot be removed for the bundle to translate;
    - "binario": the catalog is catalog.bin (it is unknown whether it checks the fingerprint);
    - ("lingua", lingue): there are tables, but not in the source language;
    - None: the game does not use Unity Localization.
    If the catalog (catalog.json) checks the fingerprint of a bundle to translate, the game profile entry also has
    "catalogo": Progetto sets that bundle's CRC to 0 there before rewriting it (with backup, see
    Progetto._togli_crc)."""
    tutte = adapter_unity_loc.bundle_tabelle(cartella)
    if not tutte:
        return None
    if any(lingua.stessa_lingua(c, destinazione) for c in tutte):
        log("Il gioco ha gia' la traduzione in %s: sceglila nel menu delle lingue del gioco."
            % lingua.etichetta(destinazione))
        return "ufficiale"
    sorgente = origine if origine and origine != lingua.AUTO else "en"
    codice = lingua.migliore(tutte, sorgente)
    if codice is None:
        log("  Unity Localization: tabelle dei testi in %s, ma non in %s." % (", ".join(sorted(tutte)),
                                                                            lingua.etichetta(sorgente)))
        return "lingua", sorted(tutte)
    voci = _voci_tabelle(cartella, tutte[codice], log)
    if isinstance(voci, str):
        return voci
    log("  Unity Localization: tabelle dei testi in %d lingue (%s); traduco quella in %s (%s) dentro il suo bundle "
        "(backup automatico), senza mod" % (len(tutte), ", ".join(sorted(tutte)), lingua.etichetta(sorgente),
                                            ", ".join(r.split("/")[-1] for r in tutte[codice])))
    if esistente:
        profilo = _leggi_profilo(esistente)
        profilo.update({"motore": "unity-localization", "lingua_origine": origine, "lingua_destinazione": destinazione,
                        "file": voci, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/")})
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"),
                   "motore": "unity-localization", "lingua_origine": origine, "lingua_destinazione": destinazione,
                   "backend": "locale", "file": voci}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, profilo)
    log("== 2/3 Unity Localization: traduco le tabelle dei testi (nel gioco lascia selezionato %s)"
        % (lingua.endonimo(sorgente) or lingua.etichetta(sorgente)))
    return percorso


def _prepara_unity(cartella, esistente, origine, destinazione, esito, log, backend="google"):
    """Game profile for a Unity game: the translation happens inside the game with XUnity.AutoTranslator; for IL2CPP
    games with Unity Localization the text tables inside the bundles are translated.

    The game profile keeps the chosen translator (backend: the translator started by the game uses it too) and who
    translates the new sentences while the game is played (dal_vivo, see DAL_VIVO_SCELTE): so "Reapply" and the
    command line redo the same thing even after "Restore originals"."""
    il2cpp = motore_unity(esito) == "il2cpp"
    if ha_anticheat(esito):  # first of all, for IL2CPP games too (their bundles are game files)
        log("Questo gioco ha un anti-cheat: %s (rischio di ban)." % (
            "non modifico i suoi file" if il2cpp else "non installo un loader di mod"))
        return None, _segna(esito, "ANTICHEAT", MOTIVO_ANTICHEAT, motore="unity")
    if il2cpp:
        radice = adapter_unity.trova_radice(cartella) or cartella
        tabelle = _prepara_unity_tabelle(radice, esistente or trova_profilo(radice), origine, destinazione, log)
        if tabelle == "ufficiale":
            return None, _segna(esito, "UFFICIALE", _motivo_ufficiale(destinazione), motore="unity-localization",
                                azioni=["scegli_lingua"])
        if tabelle == "crc":
            return None, _segna(esito, "NON_SUPPORTATO", MOTIVO_CRC, motore="unity-localization",
                                azioni=["crea_rapporto"])
        if tabelle == "binario":
            return None, _segna(esito, "NON_SUPPORTATO", MOTIVO_CATALOGO_BINARIO, motore="unity-localization",
                                azioni=["crea_rapporto"])
        if isinstance(tabelle, tuple):  # tables, but not in the source language
            sorgente = origine if origine and origine != lingua.AUTO else "en"
            return None, _segna(esito, "NON_SUPPORTATO", MOTIVO_TABELLE_SENZA_ORIGINE % (
                lingua.etichetta(sorgente), ", ".join(dict.fromkeys(_nome_lingua(c) for c in tabelle[1]))),
                motore="unity-localization", azioni=["crea_rapporto"])
        if tabelle:
            return tabelle, esito
        log("Gioco Unity IL2CPP senza tabelle di Unity Localization: non ancora supportato (servirebbe BepInEx 6).")
        return None, _segna(esito, "NON_SUPPORTATO", MOTIVO_IL2CPP, motore="unity", azioni=["crea_rapporto"])
    radice = adapter_unity.trova_radice(cartella)
    if not radice:
        log("Non trovo l'eseguibile del gioco Unity (il .exe con accanto la cartella _Data).")
        return None, _segna(esito, "CARTELLA_SBAGLIATA", "Non trovo l'eseguibile del gioco Unity (il file .exe con "
                            "accanto la cartella che finisce con _Data): scegli la cartella dove c'e' l'eseguibile.",
                            motore="unity")
    if _normalizza(radice) != _normalizza(cartella):
        log("Cartella del gioco: %s" % radice)
        cartella = radice
        esistente = esistente or trova_profilo(cartella)
    if esistente:
        profilo = _leggi_profilo(esistente)
        if profilo.get("motore") != "unity" and profilo.get("file"):
            log("  (il profilo aveva anche traduzioni di file di testo: con Unity si usa solo la traduzione dentro il "
                "gioco)")
        profilo.update({"motore": "unity", "lingua_origine": origine, "lingua_destinazione": destinazione,
                        "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"), "backend": backend})
        if profilo.get("dal_vivo") not in DAL_VIVO_SCELTE:
            profilo["dal_vivo"] = "servizio"
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"),
                   "motore": "unity", "lingua_origine": origine, "lingua_destinazione": destinazione,
                   "backend": backend, "dal_vivo": "servizio", "file": []}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    profilo["file"] = _tabelle_mono(cartella, origine, destinazione, log)
    scansione.salva_json(percorso, profilo)
    log("== 2/3 Unity Mono: i testi verranno tradotti dentro il gioco da XUnity.AutoTranslator (con BepInEx)")
    return percorso, esito


def _unisci(vecchio, nuovo, log):
    """Adds the new files to the existing game profile and removes those the game no longer has."""
    cartella = vecchio["cartella_gioco"]
    tenuti = []
    for f in vecchio["file"]:
        if os.path.exists(os.path.join(cartella, *f["percorso"].split("/"))):
            tenuti.append(f)
        else:
            log("  - %s non esiste piu' nel gioco: tolto dal profilo" % f["percorso"])
    presenti = {f["percorso"] for f in tenuti}
    aggiunti = [f for f in nuovo["file"] if f["percorso"] not in presenti]
    vecchio["file"] = tenuti + aggiunti
    for chiave in ("lingua_origine", "lingua_destinazione", "backend"):
        vecchio[chiave] = nuovo[chiave]
    return vecchio, aggiunti


def prepara(cartella, origine="auto", destinazione=None, backend="google", log=print, forza=False, rigenera=False,
            traduci_comunque=False, consenso=False):
    """Detects the engine, finds the texts and creates/updates the game profile. Returns (percorso_profilo, esito)."""
    percorso, esito = _prepara(cartella, origine, destinazione, backend, log, forza, rigenera, traduci_comunque,
                               consenso)
    if percorso:
        _nomi_nel_profilo(percorso, log)
        _identita_nel_profilo(percorso)
    if traduci_comunque and percorso:
        # machine translation wanted (and possible): if we had set the official language I put it back as it was,
        # otherwise the game would stay on the official language and the installed translation would not be seen. If
        # there is nothing to translate the official language stays.
        with open(percorso, encoding="utf-8") as f:
            registrata = json.load(f).get("cartella_gioco")
        for d in dict.fromkeys(os.path.abspath(x) for x in (cartella, registrata) if x):
            if lingua_ufficiale.impostata(d) and lingua_ufficiale.ripristina(d, log):
                log("  La traduzione automatica prende il posto della lingua originale del gioco.")
    return percorso, esito


def _nomi_nel_profilo(percorso, log):
    """The game's names not to translate (studio and title: see names.py) in the game profile, profilo["non_tradurre"],
    together with those already there (added by hand too). Progetto protects them like the game's codes. The names
    found also stay in profilo["nomi_trovati"]: a name found once and then removed by hand from "non_tradurre" (for
    example a title you want translated) does not come back."""
    try:
        profilo = _leggi_profilo(percorso)
        trovati = nomi.nomi_del_gioco(profilo["cartella_gioco"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    gia = [n for n in profilo.get("non_tradurre") or [] if isinstance(n, str) and n.strip()]
    visti = profilo.get("nomi_trovati") if isinstance(profilo.get("nomi_trovati"), list) else []
    nuovi = {"non_tradurre": list(dict.fromkeys(gia + [n for n in trovati if n not in visti])),
             "nomi_trovati": list(dict.fromkeys(visti + trovati))}
    if any(profilo.get(k, []) != v for k, v in nuovi.items()):
        profilo.update(nuovi)
        scansione.salva_json(percorso, profilo)
    elenco = nuovi["non_tradurre"]
    if elenco:
        log("  Nomi del gioco che restano come sono nella traduzione: %s" % ", ".join(elenco))


def _identita_nel_profilo(percorso):
    """The game's identity (see identita) in the game profile: used to recognize it if you move or copy it elsewhere."""
    try:
        profilo = _leggi_profilo(percorso)
        nuova = identita(profilo["cartella_gioco"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    if profilo.get("identita") != nuova:
        profilo["identita"] = nuova
        scansione.salva_json(percorso, profilo)


def _prepara(cartella, origine, destinazione, backend, log, forza, rigenera, traduci_comunque, consenso):
    cartella = os.path.abspath(cartella)
    if not os.path.isdir(cartella):
        raise ValueError("La cartella non esiste: %s" % cartella)
    destinazione = destinazione or lingua.lingua_di_windows()
    esistente = trova_profilo(cartella)
    if esistente:
        with open(esistente, encoding="utf-8") as f:
            registrata = json.load(f)["cartella_gioco"]
        if _normalizza(registrata) != _normalizza(cartella):
            log("Questo gioco e' gia' registrato con la cartella %s: uso quella." % registrata)
            cartella = registrata

    log("== 1/3 Riconoscimento del motore")
    esito = rilevamento.rileva(cartella)
    rilevamento.stampa(esito, lambda m: log(DETTAGLIO + "  " + m))
    log("  Motore: %s" % (esito.get("motore_principale") or "non riconosciuto"))
    supportato, web = gioco_supportato(esito)
    if not traduci_comunque:
        # does the game already have the official translation? Then that one is used, not a machine translation
        ufficiale = lingua_ufficiale.rileva(cartella, destinazione, esito.get("motore_principale"), log)
        if ufficiale["presente"]:
            log("== Lingua ufficiale")
            interruzione.controlla()
            fatto, impostazione = lingua_ufficiale.imposta(cartella, ufficiale, destinazione, log, consenso)
            righe = lingua_ufficiale.messaggio(ufficiale, destinazione, impostazione, fatto)
            installata = traduzione_installata(cartella)
            if installata:
                righe.append("E' ancora installata la traduzione automatica di PatchSmith: se il gioco mostra quella "
                             "invece della lingua ufficiale, premi 'Ripristina originali' e poi di nuovo 'Analizza "
                             "e traduci'.")
            for riga in righe:
                log("  " + riga)
            esito = dict(esito, lingua_ufficiale=ufficiale, lingua_ufficiale_righe=righe,
                         traduzione_installata=installata, motore_supportato=bool(supportato or web),
                         lingua_impostata=fatto, serve_consenso=fatto is None)
            return None, _segna(esito, "UFFICIALE", righe[0] if righe else "", azioni=["scegli_lingua"],
                                extra={"lingua_ufficiale": True, "righe": righe, "supportato": bool(supportato or web),
                                       "serve_consenso": fatto is None, "impostata": fatto})
        for nota in ufficiale["note"]:
            log("  Nota: " + nota)
    if motore_unity(esito):
        return _prepara_unity(cartella, esistente, origine, destinazione, esito, log, backend)
    if motore_unreal(esito):
        return _prepara_unreal(cartella, esistente, origine, destinazione, esito, log, backend)
    if motore_xna(esito):
        risultato = _prepara_xna(cartella, esistente, origine, destinazione, esito, log, backend, traduci_comunque)
        if risultato is not None:
            return risultato
    if motore_gamemaker(esito):
        risultato = _prepara_gamemaker(cartella, esistente, origine, destinazione, esito, log, backend,
                                       traduci_comunque)
        if risultato is not None:
            return risultato
    if motore_godot(esito):
        percorso, risultato = _prepara_godot(cartella, esistente, origine, destinazione, esito, log, backend)
        if percorso or risultato is not None:
            return percorso, risultato

    cifrato = rilevamento.contenitore_cifrato(esito)
    if cifrato and adapter_web_vivo.adatto(cartella):  # the texts are translated live, in the game's page
        return _prepara_web_vivo(cartella, esistente, origine, destinazione, backend, log), esito
    if cifrato:  # before looking for texts: the only readable ones would be others' files (e.g. a Steam emulator)
        log("  " + MOTIVO_CIFRATO % cifrato)
        return None, _segna(esito, "NON_SUPPORTATO", MOTIVO_CIFRATO % cifrato, motore="web", azioni=["crea_rapporto"])
    log("== 2/3 Ricerca dei testi")
    candidati, altri = scansione.scansiona(cartella, log)
    candidati, tolti, arrivo = _solo_lingua_originale(candidati, cartella, origine, destinazione, log)
    # CSV tables with the target language column filled: official translation
    arrivo += [c["percorso"] for c in candidati if c.get("formato") == "csv" and destinazione and any(
        lingua.stessa_lingua(x, destinazione) for x in c.get("lingue", ()) if x != c.get("lingua"))]
    if arrivo and not traduci_comunque:
        log("Il gioco ha gia' la traduzione in %s (%s): sceglila nel menu delle lingue del gioco."
            % (lingua.etichetta(destinazione), ", ".join(sorted(arrivo)[:2])))
        return None, _segna(esito, "UFFICIALE", _motivo_ufficiale(destinazione), motore="web" if web else None,
                            azioni=["scegli_lingua", "traduci_comunque"])
    scansione.stampa(candidati, altri, lambda m: log(DETTAGLIO + "  " + m))
    nome = os.path.splitext(os.path.basename(esistente))[0] if esistente else nome_per_cartella(cartella)
    # the detected engine stays in the game profile: in GameMaker and XNA the translations adapt to the font characters
    nuovo = scansione.crea_profilo(nome, cartella, candidati, origine, destinazione, backend,
                                   esito["radici_web"] if web else (), motore=esito.get("motore_principale"), log=log)

    if not nuovo["file"]:
        log("Nessun testo traducibile trovato con gli strumenti attuali.")
        for c in esito["consigli"]:
            log("  * " + c)
        return None, _segna(esito, "NON_SUPPORTATO", _motivo_nessun_testo(esito, cartella), azioni=["crea_rapporto"],
                            motore=(esito.get("motore_principale") or "").lower() or None)
    if not supportato and not forza:
        log("Motore %s: non ancora supportato in automatico." % (esito["motore_principale"] or "non riconosciuto"))
        for c in esito["consigli"]:
            log("  * " + c)
        log("Ho trovato comunque %d file con testo: puoi tradurli in modo sperimentale." % len(nuovo["file"]))
        motore = esito["motore_principale"] or "non riconosciuto"
        return None, _segna(dict(esito, sperimentale_possibile=True), "NON_SUPPORTATO",
                            "Il motore del gioco (%s) non e' ancora supportato in automatico." % motore,
                            motore=(esito.get("motore_principale") or "").lower() or None,
                            azioni=["sperimentale", "crea_rapporto"], extra={"motore_rilevato": motore})

    if esistente and not rigenera:
        with open(esistente, encoding="utf-8") as f:
            profilo, aggiunti = _unisci(json.load(f), nuovo, log)
        for chiave in ("motore", "motore_gioco"):  # game profiles created before the engine stayed in the profile
            if nuovo.get(chiave) and not profilo.get(chiave):
                profilo[chiave] = nuovo[chiave]
        esclusi = [f["percorso"] for f in profilo["file"] if f["percorso"] in tolti]
        if esclusi:
            profilo["file"] = [f for f in profilo["file"] if f["percorso"] not in tolti]
            log("  Tolti dal profilo (traduzioni ufficiali del gioco): %s" % ", ".join(esclusi))
            _rimetti_esclusi(cartella, esclusi, log)
        log(DETTAGLIO + "Profilo esistente aggiornato: %s (%d file nuovi)" % (esistente, len(aggiunti)))
        percorso = esistente
    else:
        profilo = nuovo
        percorso = esistente or os.path.join(cartella_profili(), nome + ".json")
        log(DETTAGLIO + "Profilo creato: %s" % percorso)
    scansione.salva_json(percorso, profilo)
    for f in profilo["file"]:
        tipo = "interfaccia (menu)" if f.get("formato") == "web-ui" else \
            "tabella dei testi (colonna %s)" % f.get("colonna") if f.get("formato") == "csv" else \
            "%s (%d campi)" % ("file di lingua" if f.get("localizzazione") else "dati", len(f.get("includi", [])))
        log(DETTAGLIO + "  - %s: %s" % (f["percorso"], tipo))
    log("  File con testi da tradurre: %d" % len(profilo["file"]))
    if profilo.get("motore_gioco"):
        log("  Le traduzioni si adattano ai caratteri che i font del gioco sanno disegnare")
    return percorso, esito


def _prepara_web_vivo(cartella, esistente, origine, destinazione, backend, log):
    """Game profile of an Electron game with encrypted files but a plain preload.js (see adapter_web_vivo): no files
    to translate, the texts are translated while the game is played. Returns the game profile's path."""
    if esistente:
        profilo = _leggi_profilo(esistente)
        if profilo.get("file"):  # (e.g. files of a Steam emulator translated by mistake before): no longer used
            log(DETTAGLIO + "  Tolti dal profilo: %s" % ", ".join(f["percorso"] for f in profilo["file"]))
        profilo.update({"motore": "web-vivo", "file": [], "lingua_origine": origine, "lingua_destinazione": destinazione,
                        "backend": backend, "dal_vivo": "servizio"})
        percorso = esistente
    else:
        nome = nome_per_cartella(cartella)
        profilo = {"nome": nome, "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"), "motore": "web-vivo",
                   "lingua_origine": origine, "lingua_destinazione": destinazione, "backend": backend,
                   "dal_vivo": "servizio", "file": []}
        percorso = os.path.join(cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, profilo)
    log("== 2/3 Testi cifrati: li traduce PatchSmith mentre giochi, nella pagina del gioco (il file cifrato non si "
        "tocca)")
    return percorso


def traduzione_dal_vivo(cartella):
    """(profilo, attiva) if the folder's game has translation while playing (Unity, live web), otherwise (profilo or
    None, None)."""
    percorso = trova_profilo(cartella)
    if not percorso:
        return None, None
    try:
        profilo = _leggi_profilo(percorso)
    except (OSError, ValueError):
        return None, None
    if profilo.get("motore") not in ("unity", "web-vivo"):
        return profilo, None
    return profilo, profilo.get("dal_vivo") != "nessuno"


def imposta_dal_vivo(cartella, attiva, log=print):
    """Turns translation while playing on or off (the choice at the end of "Analyze and translate"): it stays in the
    game profile and is applied to the game right away if it is closed (if it is open, its translator stops and the
    rest applies from the next check at startup or from "Reapply"). Returns True if applied in the game."""
    from . import xunity_service as servizio_xunity
    percorso = trova_profilo(cartella)
    profilo = _leggi_profilo(percorso)
    profilo["dal_vivo"] = "servizio" if attiva else "nessuno"
    scansione.salva_json(percorso, profilo)
    if not attiva and servizio_xunity.ferma(profilo["nome"]):
        log("Traduttore del gioco fermato.")
    gioco = profilo["cartella_gioco"]
    if sistema.gioco_aperto(gioco):
        log("Il gioco e' aperto: la scelta vale dalla prossima volta che lo avvii.")
        return False
    if profilo.get("motore") == "web-vivo":
        web_vivo_completo(percorso, log=lambda m: log(DETTAGLIO + m) if m.strip() else None, rete=False,
                          backend=None)
    else:
        da = profilo.get("lingua_origine")  # the one XUnity is already configured with, if "auto"
        ini = adapter_unity.leggi_ini(os.path.join(gioco, *adapter_unity.CONFIG_XUNITY.split("/")))
        if da in (None, lingua.AUTO):
            da = (ini.get("General") or {}).get("FromLanguage") or "en"
        adapter_unity.installa(gioco, da, profilo["lingua_destinazione"],
                               profilo["nome"], lambda m: log(DETTAGLIO + m),
                               locale=percorso if attiva else "nessuno")
    log("Traduzione mentre giochi: %s." % ("accesa" if attiva else "spenta (solo le traduzioni gia' salvate)"))
    return True


def web_vivo_completo(percorso_profilo, log=print, rete=True, backend="google", esito=None):
    """Installs (or updates) the live translation of a web game with encrypted files: the script in preload.js and the
    translations already made (the game's translation memory). No pre-translation: the texts are known only while the
    game is played."""
    profilo = _leggi_profilo(percorso_profilo)
    cartella, a = profilo["cartella_gioco"], profilo["lingua_destinazione"]
    da = profilo.get("lingua_origine")
    da = "en" if da in (None, lingua.AUTO) else da  # (the texts are encrypted: the language cannot be detected first)
    if sistema.gioco_aperto(cartella):
        log("Il gioco e' aperto: chiudilo e riprova.")
        _segna(esito, "GIOCO_APERTO", "Il gioco e' aperto.", motore="web", azioni=["chiudi_gioco"])
        return False
    if rete and backend not in (None, "finto") and profilo.get("backend") != backend:
        profilo["backend"] = backend  # used by the translator started by the game
        scansione.salva_json(percorso_profilo, profilo)
    progetto = Progetto(percorso_profilo, log, prova=backend == "finto")
    attivo = profilo.get("dal_vivo") != "nessuno"
    n = adapter_web_vivo.installa(cartella, percorso_profilo, profilo["nome"], da, a, progetto.memoria, log,
                                  attivo=attivo)
    from . import xunity_service as servizio_xunity
    previsti = servizio_xunity.traduttori_previsti(profilo) if attivo else []
    dal_vivo = "locale" if "locale" in previsti else ("online" if previsti else "nessuno")
    log("  Frasi mentre giochi: %s" % DAL_VIVO.get(dal_vivo, dal_vivo))
    _segna(esito, "OK", MOTIVO_WEB_VIVO, motore="web", scritto=True, tradotti=n, dal_vivo=dal_vivo,
           azioni=["installa_locale"] if dal_vivo == "nessuno" else [])
    return True


def analizza_e_traduci(cartella, origine="auto", destinazione=None, backend="google", log=print,
                       forza=False, rigenera=False, traduci_comunque=False, consenso=False, riusa=None):
    """Complete procedure. Returns (percorso_profilo, completato, esito); the outcome for the interface (code, counts,
    reason, actions: see outcome.py) is in esito["esito"]. riusa: the game profile of the same game in another folder
    (see stessi_giochi) whose translations to reuse: if that folder is no longer there the game profile moves to this
    one, otherwise its translations are copied into the new game profile before translating."""
    spostato = bool(riusa) and _sposta_profilo(riusa, cartella, log)
    percorso, esito = prepara(cartella, origine, destinazione, backend, log, forza, rigenera, traduci_comunque,
                              consenso)
    interruzione.controlla()  # stopped during the analysis: no questions or writes afterwards
    if not percorso:
        if "esito" not in esito:  # every exit has its own outcome: this is only a fallback
            _segna(esito, "NON_SUPPORTATO", "Non ho trovato un modo sicuro per tradurre questo gioco.",
                   azioni=["crea_rapporto"])
        return None, False, esito
    if riusa and not spostato:
        _copia_traduzioni(riusa, percorso, log)
    profilo = _leggi_profilo(percorso)
    if profilo.get("motore") == "godot":
        log("== 3/3 Traduzione della tabella dei testi (i file del gioco non vengono toccati)")
        pronto = godot_completo(percorso, log, rete=True, backend=backend, esito=esito)
        if pronto:
            log("Fatto. Avvia il gioco con la lingua %s selezionata: i testi compaiono in %s." % (
                lingua.etichetta(profilo["godot"]["lingua"]), lingua.etichetta(profilo["lingua_destinazione"])))
        elif adapter_godot.manifesto_presente(os.path.dirname(os.path.join(
                profilo["cartella_gioco"], *profilo["godot"]["pck"].split("/")))):
            log("Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
        return percorso, bool(pronto), esito
    if profilo.get("motore") == "web-vivo":
        log("== 3/3 Traduzione dal vivo (backup automatico di preload.js)")
        pronto = web_vivo_completo(percorso, log, rete=True, backend=backend, esito=esito)
        if pronto:
            log("Fatto. Avvia il gioco: le frasi si traducono mentre giochi (la prima volta compaiono in inglese per "
                "un attimo).")
        return percorso, bool(pronto), esito
    if profilo.get("motore") == "unity":
        log("== 3/3 Installazione di BepInEx + XUnity.AutoTranslator (nessun file del gioco viene modificato)")
        pronto = unity_completo(percorso, log, rete=True, anticheat=ha_anticheat(esito), backend=backend,
                                esito=esito)
        if pronto is False and adapter_unity.manifesto_presente(profilo["cartella_gioco"]):
            log("Fatto in parte: XUnity e' installato, ma la pre-traduzione non e' completa (vedi sopra).")
        if pronto:
            nessuno = getattr(esito.get("esito"), "dal_vivo", None) == "nessuno"
            log("Fatto. Avvia il gioco: le frasi pre-tradotte compaiono subito in italiano; le altre (es. testi")
            log("composti al momento) %s. Nel gioco: ALT+T alterna originale/tradotto."
                % ("per ora restano in originale (vedi sopra)" if nessuno else "vengono tradotte dal vivo"))
        return percorso, bool(pronto), esito
    if profilo.get("motore") == "unreal":
        log("== 3/3 Traduzione dei .locres e scrittura del pak di patch (i pak originali non vengono toccati)")
        pronto = unreal_completo(percorso, log, rete=True, backend=backend, esito=esito)
        if pronto:
            log("Fatto. Nel gioco deve essere selezionata la lingua originale (di solito English): la traduzione")
            log("prende il suo posto. Per tornare all'originale: 'Ripristina originali'.")
        elif adapter_unreal.manifesto_presente(profilo["cartella_gioco"]):
            log("Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
            _dopo_i_tentativi(esito, log)
        return percorso, bool(pronto), esito
    if profilo.get("motore") == "xna":
        log("== 3/3 Traduzione delle risorse .NET e creazione dei satelliti (i file del gioco non vengono toccati)")
        pronto = xna_completo(percorso, log, rete=True, backend=backend, esito=esito)
        if pronto:
            log("Fatto. Nel gioco lascia (o seleziona) la lingua originale, di solito English: la traduzione")
            log("prende il suo posto. Per tornare all'originale: 'Ripristina originali'.")
        elif adapter_xna.manifesto_presente(profilo["cartella_gioco"]):
            log("Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
            _dopo_i_tentativi(esito, log)
        return percorso, bool(pronto), esito
    log("== 3/3 Traduzione e scrittura nel gioco (backup automatico degli originali)")
    if backend == "finto":
        log("  (prova senza rete: le traduzioni finte vanno in una memoria separata)")
    p = Progetto(percorso, log, prova=(backend == "finto"))
    completato = p.traduci(crea_backend(backend, log))
    log("Fatto. Avvia il gioco per vedere la traduzione." if completato else
        "Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
    if profilo.get("motore") == "gamemaker":
        _dopo_la_scrittura(p.profilo, log, esito, imposta=backend != "finto")
    if profilo.get("motore") == "unity-localization":
        sorgente = profilo.get("lingua_origine") if profilo.get("lingua_origine") not in (None, lingua.AUTO) else "en"
        log("Nel gioco lascia selezionata la lingua %s: i suoi testi ora sono tradotti."
            % (lingua.endonimo(sorgente) or lingua.etichetta(sorgente)))
    _esito_applica(esito, p, profilo.get("motore") or ("web" if gioco_supportato(esito)[1] else None))
    if not completato:
        _dopo_i_tentativi(esito, log)
    return percorso, completato, esito


def _motivo_gia_in(destinazione):
    return "Il gioco e' gia' in %s: non c'e' niente da tradurre." % lingua.etichetta(destinazione)


def _motivo_ufficiale(destinazione):
    return "Il gioco ha gia' la traduzione ufficiale in %s." % lingua.etichetta(destinazione)


def _motivo_nessun_testo(esito, cartella):
    """Why there is nothing to translate, worded for the type of game (what the detection saw)."""
    motore = esito.get("motore_principale")
    contenitori = {r["nome"] for r in esito["risultati"] if r["sezione"] == "Container"}
    if motore == "GameMaker":
        return ("I testi di questo gioco GameMaker sono dentro il file dei dati del gioco (data.win), non in file di "
                "testo separati: PatchSmith non sa ancora tradurli.")
    if motore in ("XNA", "FNA", "MonoGame"):
        return ("I testi di questo gioco sono scritti nel suo codice, non in file di lingua separati: PatchSmith non "
                "sa ancora tradurli.")
    if any("!/" in r for r in esito.get("radici_web") or ()) or \
            ("Electron" in contenitori and os.path.exists(os.path.join(cartella, "resources", "app.asar"))):
        return ("I file del gioco sono chiusi in un archivio (app.asar): PatchSmith non sa ancora tradurre i testi "
                "che ci sono dentro.")
    if motore:
        return ("Non ho trovato file con testi che PatchSmith sappia leggere (motore del gioco: %s)." % motore)
    return "Non ho riconosciuto il motore del gioco e non ho trovato file con testi da tradurre."


def _esito_scrittura(esito, progetto, testi, scritto, motore, dal_vivo=None, motivo="", azioni=()):
    """Outcome of the engines that write the translations from the translation memory into the game (Unity, Unreal,
    XNA): OK, PARZIALE or TRADUTTORE_BLOCCATO, with the translated texts and those left untranslated. azioni: extra
    ones (e.g. for Unity's new sentences without a translator)."""
    if esito is None:
        return
    unici = list(dict.fromkeys(t for t in testi if isinstance(t, str)))
    rimasti = [t for t in unici if not (isinstance(progetto.memoria.get(t), str) and progetto.memoria.get(t))]
    campi = dict(motore=motore, scritto=scritto, tradotti=len(unici) - len(rimasti), rimasti=len(rimasti),
                 esempi_rimasti=rimasti[:20], dal_vivo=dal_vivo, extra=_extra_tentativi(progetto))
    if progetto.esito_traduzione == "TRADUTTORE_BLOCCATO" and rimasti:
        _segna(esito, "TRADUTTORE_BLOCCATO", progetto.motivo_traduzione, azioni=["usa_locale"] + list(azioni),
               **campi)
    elif rimasti or not scritto:
        _segna(esito, "PARZIALE", motivo or ("" if scritto else "Non ho scritto nulla nel gioco."),
               azioni=["usa_locale"] + list(azioni), **campi)
    else:
        _segna(esito, "OK", motivo, azioni=list(azioni), **campi)


def _esito_applica(esito, progetto, motore):
    """Outcome after Progetto.traduci or applica (games with text files: web, GameMaker, Unity Localization...)."""
    if esito is None:
        return
    r = progetto.ultimo_applica or {"ok": 0, "mancanti": 0, "esempi": [], "scartate": 0, "scritti": 0,
                                    "aperto": False}
    # scritto: the game has at least one translation (a file rewritten without translations does not count)
    campi = dict(motore=motore, scritto=r["scritti"] > 0 and r["ok"] > 0, tradotti=r["ok"], rimasti=r["mancanti"],
                 esempi_rimasti=r["esempi"], extra=_extra_tentativi(progetto))
    if esito.get("lingua_aggiunta"):
        campi["extra"]["lingua_aggiunta"] = esito["lingua_aggiunta"]
    if r["aperto"]:
        _segna(esito, "GIOCO_APERTO", "Il gioco e' aperto: le traduzioni sono pronte e salvate, ma non le ho scritte "
               "nel gioco.", azioni=["chiudi_gioco", "riapplica"], **campi)
    elif progetto.esito_traduzione == "NULLA_DA_FARE":
        _segna(esito, "NULLA_DA_FARE", progetto.motivo_traduzione, **campi)
    elif progetto.esito_traduzione == "TRADUTTORE_BLOCCATO" and r["mancanti"]:
        _segna(esito, "TRADUTTORE_BLOCCATO", progetto.motivo_traduzione, azioni=["usa_locale"], **campi)
    elif r["mancanti"]:
        motivo = ("%s perche' il gioco non %s mostrerebbe bene (forma diversa dall'originale o caratteri che i suoi "
                  "font non hanno)." % ("Una traduzione non e' stata scritta" if r["scartate"] == 1 else
                                        "%d traduzioni non sono state scritte" % r["scartate"],
                                        "la" if r["scartate"] == 1 else "le")) if r["scartate"] else ""
        if r["scartate"]:  # (for "Fix translations": the "Not written to the game" list, see outcome.py)
            campi["extra"]["scartate"] = r["scartate"]
        # texts never translated (not just rejected): another translator may help
        _segna(esito, "PARZIALE", motivo, azioni=["usa_locale"] if r["mancanti"] > r["scartate"] else [], **campi)
    else:
        _segna(esito, "OK", **campi)


def _dopo_la_scrittura(profilo, log, esito, imposta=True):
    """GameMaker, after writing the translations: if the translation is an added language, sets it in the options
    saved by the game (or says how to choose it); otherwise says to leave the original language."""
    cartella = profilo["cartella_gioco"]
    voci = [v for v in profilo.get("file", []) if v.get("lingua_nuova")]
    scritte = [v for v in voci if os.path.exists(os.path.join(cartella, *v["lingua_nuova"].split("/")))]
    if voci and not scritte:
        log("La traduzione NON e' nel gioco (vedi sopra: se il gioco era aperto, chiudilo e premi di nuovo 'Analizza "
            "e traduci', che usa le traduzioni gia' salvate).")
        return
    if not voci:
        log("Nel gioco lascia selezionata la lingua %s: i suoi testi ora sono tradotti."
            % lingua.etichetta(profilo.get("lingua_file") or "en"))
        return
    nome = os.path.splitext(os.path.basename(scritte[0]["lingua_nuova"]))[0]
    impostata = False
    if imposta:
        impostata, righe = lingua_ufficiale.imposta_lingua_aggiunta(
            cartella, nome, lingue_della_cartella(cartella, scritte[0]["percorso"]), log,
            sorgente=os.path.splitext(os.path.basename(scritte[0]["percorso"]))[0])
        for r in righe:
            log("  " + r)
    esito["lingua_aggiunta"] = {"nome": nome, "impostata": impostata}
    log("Nel gioco la traduzione e' la lingua '%s': %s. L'originale resta selezionabile nel menu." % (
        nome, "l'ho gia' impostata nelle opzioni del gioco" if impostata else
        "sceglila in Opzioni > Lingua (con le frecce)"))


def traduzione_installata(cartella):
    """True if the folder has a PatchSmith machine translation (modified files or added components)."""
    try:
        radice = adapter_unity.trova_radice(cartella)
        tradotti = [p for p in trova_info(cartella) if not configuratore.file_del_configuratore(p)]
        return bool(tradotti or adapter_unity.manifesto_presente(cartella) or
                    (radice and adapter_unity.manifesto_presente(radice)) or adapter_unreal.manifesto_presente(cartella)
                    or adapter_xna.installato(cartella))
    except (OSError, ValueError):
        return False


def ripristina(cartella, log=print, esito=None):
    """Puts back the originals of all the modified files, even without a game profile; the game's language last.
    With the game open it touches nothing (files in use cannot be put back and the game would rewrite its
    settings). Returns True if everything is fine; the outcome (see outcome.py) goes into esito["esito"]."""
    percorso = trova_profilo(cartella)
    radice = registrata = cartella
    if percorso:
        with open(percorso, encoding="utf-8") as f:
            radice = registrata = json.load(f)["cartella_gioco"]
        # also look in the chosen folder, if it contains the registered one
        if _dentro(radice, cartella):
            radice = cartella
    for d in dict.fromkeys(os.path.abspath(x) for x in (cartella, registrata, radice) if x and os.path.isdir(x)):
        try:
            aperto = sistema.gioco_aperto(d)
        except sistema.ErroreSistema as e:
            log(str(e))
            _segna(esito, "GIOCO_APERTO", str(e), azioni=["chiudi_gioco"])
            return False
        if aperto:
            log("Il gioco e' aperto: chiudilo e ripeti 'Ripristina originali'. Non ho toccato nulla.")
            _segna(esito, "GIOCO_APERTO", "Il gioco e' aperto.", azioni=["chiudi_gioco"])
            return False
    modificati = trova_info(radice)
    candidati = [registrata, radice, cartella, adapter_unity.trova_radice(cartella)]
    if not percorso:  # parent folder with several registered games: I consider all of them
        candidati += [p.get("cartella_gioco") for _, p in elenco_profili()
                      if p.get("cartella_gioco") and _dentro(p["cartella_gioco"], cartella)]
    unity = list(dict.fromkeys(os.path.abspath(d) for d in candidati
                               if d and adapter_unity.manifesto_presente(d)))
    unreal = list(dict.fromkeys(os.path.abspath(d) for d in candidati
                                if d and adapter_unreal.manifesto_presente(d)))
    xna = list(dict.fromkeys(os.path.abspath(d) for d in candidati
                             if d and os.path.isdir(d) and adapter_xna.installato(d)))
    godot = list(dict.fromkeys(os.path.abspath(d) for d in candidati
                               if d and adapter_godot.manifesto_presente(d)))
    lingue = list(dict.fromkeys(os.path.abspath(d) for d in candidati if d and lingua_ufficiale.impostata(d)))
    if not modificati and not unity and not unreal and not xna and not godot and not lingue:
        log("Nessun file modificato in questa cartella: il gioco e' gia' originale.")
        _segna(esito, "GIA_ORIGINALE", "Non ho trovato modifiche di PatchSmith in questa cartella: il gioco e' gia' "
               "originale.")
        return False
    nome = os.path.splitext(os.path.basename(percorso))[0] if percorso else nome_per_cartella(radice)
    for d in unity:
        adapter_unity.disinstalla(d, nome, log)
    for d in unreal:
        adapter_unreal.disinstalla(d, log)
    for d in xna:
        adapter_xna.disinstalla(d, log)
    for d in godot:
        adapter_godot.disinstalla(d, log)
    for p in modificati:  # added languages (e.g. local/italiano.json): the game's options must not keep them
        info = leggi_info(p) or {}
        aggiunte = [os.path.splitext(n)[0] for n in info.get("creati", []) if n.lower().endswith(".json")]
        if aggiunte and gamemaker.radici(radice):
            sorgente = os.path.splitext(os.path.basename(p))[0]
            nomi = {os.path.splitext(n)[0] for n in os.listdir(os.path.dirname(p)) if n.lower().endswith(".json")}
            if lingua_ufficiale.rimetti_lingua_aggiunta(radice, aggiunte, sorgente, nomi, log) is False:
                log("Non ho tolto la traduzione: il gioco ripartirebbe con una lingua che non c'e' piu'. Chiudi il "
                    "gioco, controlla che le sue opzioni non siano di sola lettura e ripeti 'Ripristina originali'.")
                _segna(esito, "PERMESSI", "Non ho tolto la traduzione: non riesco a cambiare la lingua scelta nelle "
                       "opzioni del gioco, che ripartirebbe con una lingua che non c'e' piu'. Controlla che le "
                       "opzioni del gioco non siano di sola lettura.", azioni=["chiudi_gioco"])
                return False
    bloccati = []
    for p in modificati:
        try:
            ripristina_file(p, log)
        except (OSError, RuntimeError) as e:
            bloccati.append(os.path.basename(p))
            log("  ! %s non rimesso (%s)" % (os.path.basename(p), e))
    lingua_rimasta = False
    for d in lingue:  # last: a problem here must not prevent removing the translation
        try:
            if lingua_ufficiale.ripristina(d, log) is False:
                lingua_rimasta = True
        except Exception as e:
            lingua_rimasta = True
            log("  ! Non ho potuto rimettere la lingua del gioco com'era (%s: %s): sceglila nel menu del gioco."
                % (type(e).__name__, e))
    if bloccati:
        log("Restano da rimettere: %s. Chiudi i programmi che li usano (anche antivirus) e ripeti 'Ripristina "
            "originali'." % ", ".join(bloccati))
        _segna(esito, "PERMESSI", "Restano da rimettere: %s." % ", ".join(bloccati[:5]),
               azioni=["chiudi_gioco", "crea_rapporto"])
        return False
    if lingua_rimasta:
        log("Originali ripristinati, tranne la lingua del gioco (vedi sopra): ripeti 'Ripristina originali' per "
            "riprovare.")
        _segna(esito, "PARZIALE", "Originali ripristinati, tranne la lingua del gioco: sceglila nel menu del gioco, "
               "oppure premi di nuovo \"Ripristina originali\" per riprovare.")
        return False
    log("Originali ripristinati.")
    _segna(esito, "OK", "Originali ripristinati.")
    return True


def riapplica(cartella, log=print, prova=False, esito=None):
    """Rewrites the saved translations into the game, without network. True if everything is translated; the outcome
    (see outcome.py) goes into esito["esito"] (and GameMaker's added language into esito["lingua_aggiunta"])."""
    percorso = trova_profilo(cartella)
    if not percorso:
        log("Nessuna traduzione registrata per questa cartella.")
        _segna(esito, "NULLA_DA_FARE", "Per questa cartella non c'e' nessuna traduzione salvata: usa prima "
               "'Analizza e traduci'.")
        return False
    profilo = _leggi_profilo(percorso)
    if profilo.get("motore") == "godot":  # override.cfg, script and saved translations, without network
        return bool(godot_completo(percorso, log, rete=False, backend="finto" if prova else None, esito=esito))
    if profilo.get("motore") == "web-vivo":  # the script in preload.js and the saved translations, without network
        return bool(web_vivo_completo(percorso, log, rete=False, backend="finto" if prova else None, esito=esito))
    if profilo.get("motore") == "unity":  # reinstalls from the components already downloaded, with saved translations
        return bool(unity_completo(percorso, log, rete=False, backend="finto" if prova else "google", esito=esito))
    if profilo.get("motore") == "unreal":
        return bool(unreal_completo(percorso, log, rete=False, backend="finto" if prova else "google", esito=esito))
    if profilo.get("motore") == "xna":
        return bool(xna_completo(percorso, log, rete=False, backend="finto" if prova else "google", esito=esito))
    progetto = Progetto(percorso, log, prova=prova)
    fatto = progetto.applica()
    if profilo.get("motore") == "gamemaker":
        _dopo_la_scrittura(progetto.profilo, log, esito if esito is not None else {}, imposta=not prova)
    _esito_applica(esito, progetto, profilo.get("motore") or ("web" if any(
        v.get("formato") == "web-ui" for v in profilo.get("file", [])) else None))
    return fatto


def ritraduci(cartella, testi=None, backend="locale", log=print, prova=False, esito=None, modello=None):
    """"Fix translations": re-translates the chosen texts now (testi=None: all the machine translations and the texts
    left untranslated; fixes made by hand stay), see correzioni.ritraduci, and then rewrites them into the game like
    "Reapply" (with the game closed: with the game open the new translations stay saved and the outcome says to press
    "Reapply"). modello: the local translator model to use (e.g. '12b'). True if everything is fine; the outcome in
    esito["esito"], with extra["ritraduzione"] = {ritradotte, rimaste, testi, interrotta}."""
    from . import corrections as correzioni
    percorso = trova_profilo(cartella)
    if not percorso:
        log("Nessuna traduzione registrata per questa cartella.")
        _segna(esito, "NULLA_DA_FARE", "Per questa cartella non c'e' nessuna traduzione salvata: usa prima "
               "'Analizza e traduci'.")
        return False
    traduttore = correzioni.crea_traduttore("finto" if prova else backend, modello, log)
    risultato = correzioni.ritraduci(percorso, testi, traduttore, log, prova=prova)
    interruzione.controlla()
    registrata = _leggi_profilo(percorso).get("cartella_gioco") or cartella
    try:
        aperto = os.path.isdir(registrata) and sistema.gioco_aperto(registrata)
    except sistema.ErroreSistema:
        aperto = True
    if aperto:
        log("Il gioco e' aperto: le traduzioni nuove sono salvate, le scrivo nel gioco con 'Riapplica' quando e' "
            "chiuso.")
        _segna(esito, "GIOCO_APERTO", "Le traduzioni nuove sono salvate, ma il gioco e' aperto: non le ho ancora "
               "scritte nel gioco.", azioni=["chiudi_gioco", "riapplica"])
        fatto = False
    else:
        fatto = riapplica(cartella, log, prova=prova, esito=esito)
    if esito is not None and esito.get("esito") is not None:
        esito["esito"].extra["ritraduzione"] = risultato
    return fatto and not risultato["interrotta"]


class ErroreRicomincia(RuntimeError):
    """"Start over" did not start (codice: a code from esito.CODICI, for the window)."""

    def __init__(self, messaggio, codice=None):
        super().__init__(messaggio)
        self.codice = codice


def ricomincia_da_zero(cartella, log=print, prova=False, backend=None):
    """"Start over" (Fix translations): puts back the game's original files (like "Restore originals": the text scan
    must see the originals, not the translated files) and sets aside all the saved translations, the ones fixed by
    hand too, in memory/copies/<gioco>-<data>/ (nothing is lost), so that the next "Analyze and translate" (with
    rigenera) translates everything again. The saved translations are the game's translation memory with the machine
    translations, the texts with problems and the fixes to write; for Unity also the XUnity cache saved by "Restore".
    From the shared translation memory, the short texts machine-translated for this game are removed (and kept in the
    copy): otherwise they would come back the same. If the originals cannot be put back (game open, locked files) it
    touches no translation (ErroreRicomincia). backend: the translator that will translate again: if it is not there
    (not installed, missing key) or is paused, ErroreBackend before touching anything (otherwise the game would be left
    without a translation). Returns the folder of the copy (None if there was nothing to set aside)."""
    from . import corrections as correzioni
    from .backend import ErroreBackend
    from .project import percorso_problemi
    percorso = trova_profilo(cartella)
    if not percorso:
        return None
    if backend:
        traduttore = crea_backend(backend, log)
        pausa = traduttore.pausa() if hasattr(traduttore, "pausa") else None
        if pausa:
            raise ErroreBackend("%s Per ricominciare da zero serve un traduttore pronto: non ho toccato nulla." % pausa,
                                "TRADUTTORE_BLOCCATO")
    profilo = _leggi_profilo(percorso)
    gioco, a, nome = profilo.get("cartella_gioco") or cartella, profilo["lingua_destinazione"], profilo["nome"]
    rimessi = {}
    log("Ricomincia da zero: prima rimetto i file originali del gioco")
    ripristina(cartella, log, esito=rimessi)
    e = rimessi.get("esito")
    if e is not None and e.codice not in ("OK", "GIA_ORIGINALE", "PARZIALE"):
        raise ErroreRicomincia("%s Per ricominciare da zero servono i file originali: non ho messo da parte nessuna "
                               "traduzione." % (e.motivo or "Non ho potuto rimettere i file originali del gioco."),
                               e.codice if e.codice in ("GIOCO_APERTO", "PERMESSI") else None)
    memoria = percorso_memoria(profilo, prova)
    file = [memoria, percorso_problemi(profilo, prova)]
    if not prova:
        file += [memoria[:-len(".json")] + ".automatiche.json", correzioni.percorso_da_scrivere(profilo)]
        if profilo.get("motore") == "unity":
            file += [adapter_unity._percorso_cache(gioco, a), adapter_unity._cartella_memoria(nome)]
    presenti = [f for f in file if os.path.exists(f)]
    if not presenti:
        return None
    base = cartella_dati("memory", "copies", "%s-%s" % (nome, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    copia, n = base, 2
    while os.path.exists(copia):  # (twice in the same second: never mix two copies)
        copia, n = "%s-%d" % (base, n), n + 1
    os.makedirs(copia)
    for f in presenti:
        dest = os.path.join(copia, os.path.basename(f) if not os.path.normcase(f).startswith(
            os.path.normcase(os.path.abspath(gioco))) else "cache-nel-gioco-" + os.path.basename(f))
        shutil.move(f, dest)
    log("Traduzioni di prima messe da parte in %s (%d file): ricomincio da zero." % (copia, len(presenti)))
    if not prova:
        _togli_dalla_comune(profilo, memoria, copia, log)
    return copia


def _togli_dalla_comune(profilo, memoria, copia, log):
    """Start over: the entries of the shared translation memory machine-translated for this game (provenance) are
    removed, and kept in the copy; those fixed by hand stay (they apply to all games)."""
    from .project import percorso_comune, percorso_origine_comune
    nome, dest = profilo["nome"], profilo["lingua_destinazione"]
    fine = "-%s.origine.json" % dest
    for nome_file in os.listdir(os.path.dirname(memoria)):
        if not (nome_file.startswith("comune.") and nome_file.endswith(fine)):
            continue
        orig = nome_file[len("comune."):-len(fine)]
        try:
            origine = file_condiviso.leggi_json(percorso_origine_comune(orig, dest))
            comune = file_condiviso.leggi_json(percorso_comune(orig, dest))
        except (OSError, ValueError):
            continue
        if not isinstance(origine, dict) or not isinstance(comune, dict):
            continue
        tolte = [t for t, o in origine.items() if isinstance(o, dict) and o.get("gioco") == nome and not o.get("mano")]
        if not tolte:
            continue
        with open(os.path.join(copia, "comune.%s-%s.tolte.json" % (orig, dest)), "w", encoding="utf-8") as f:
            json.dump({t: comune.get(t) for t in tolte}, f, ensure_ascii=False, indent=2)
        file_condiviso.salva_unendo(percorso_comune(orig, dest), {k: v for k, v in comune.items() if k not in tolte},
                                    comune)
        file_condiviso.salva_unendo(percorso_origine_comune(orig, dest),
                                    {k: v for k, v in origine.items() if k not in tolte}, origine)
        log("  Memoria comune: tolte %d traduzioni automatiche di questo gioco (conservate nella copia)" % len(tolte))


def unity_completo(percorso_profilo, log=print, rete=True, anticheat=False, backend="google", esito=None):
    """Installs/updates XUnity and pre-translates the game's texts into its cache.

    rete=True: extracts the texts from the game's files and translates those not yet in memory with the chosen
    translator (which also becomes the game profile's one, read by the translator started by the game).
    rete=False: puts back into the cache only the translations already in memory (no connection).
    PatchSmith translates the new sentences while the game is played (see profilo["dal_vivo"] and servizio_xunity).
    The outcome (see outcome.py) goes into esito["esito"]: dal_vivo says who translates the new sentences (see
    _dal_vivo).
    """
    profilo = _leggi_profilo(percorso_profilo)
    cartella, a = profilo["cartella_gioco"], profilo["lingua_destinazione"]
    backend = "google" if backend in (None, "xunity-google") else backend
    prova = backend == "finto"
    traduttore = crea_backend(backend, log) if rete else None  # before installing: missing key = no changes
    interruzione.controlla()
    if rete and not prova and profilo.get("backend") != backend:  # the translator started by the game reads the profile
        profilo["backend"] = backend
        scansione.salva_json(percorso_profilo, profilo)
    # new sentences while playing: always to PatchSmith's translator (if the plugin compiles), even with rete=False
    # ("Reapply" after "Restore originals"); in the test it is left as it is (the test translation memory is another)
    scelta = profilo.get("dal_vivo") if profilo.get("dal_vivo") in DAL_VIVO_SCELTE else "servizio"
    locale = None if prova else (percorso_profilo if scelta == "servizio" else
                                 "nessuno" if scelta == "nessuno" else False)
    da = adapter_unity.installa(cartella, profilo["lingua_origine"], a, profilo["nome"], log, anticheat=anticheat,
                                locale=locale)
    if not da:
        _segna(esito, "NULLA_DA_FARE", _motivo_gia_in(a), motore="unity")
        return False
    progetto = Progetto(percorso_profilo, log, prova=prova)
    falliti = 0
    testi = []
    # wrong XUnity cache lines written by PatchSmith (or by its live translator): they are redone
    cache = {} if prova else _righe_cache_da_rifare(cartella, a, da, progetto, log)
    if rete:
        info = adapter_unity.analizza(cartella)
        testi = adapter_unity.estrai_testi(cartella, info, da, log)
        log("  Pre-traduzione: %d testi del gioco trovati" % len(testi))
        presenti = set(testi)
        testi += [t for t in cache if t not in presenti]
        if testi:
            try:
                falliti = progetto.traduci_testi(testi, traduttore, da, a, rifare={
                    t: "riga della cache di XUnity da correggere" for t, v in cache.items()
                    if progetto.memoria.get(t) == v})
            except interruzione.Interrotto:
                log("  BepInEx e XUnity.AutoTranslator sono gia' installati nel gioco: le frasi non pre-tradotte "
                    "verranno tradotte mentre giochi. 'Analizza e traduci' completa la pre-traduzione, 'Ripristina "
                    "originali' li toglie.")
                raise
        traduzioni = {t: progetto.memoria[t] for t in testi if t in progetto.memoria}
    else:
        traduzioni = dict(progetto.memoria)
        testi = list(traduzioni)
    # the lines to fix are rewritten only with a new translation that does not have the same problems
    nomi_gioco = progetto.nomi_attivi(list(cache))
    sostituisci = {t: v for t, v in cache.items() if isinstance(traduzioni.get(t), str) and traduzioni[t] != v
                   and not adapter_unity.controlla_cache_xunity({t: traduzioni[t]}, da, a, nomi_gioco)}
    if adapter_unity.traduttore_dal_vivo(cartella) == "google" and not adapter_unity.google_libero():
        # Google blocked the requests during the pre-translation: XUnity must not keep trying while the game is played
        adapter_unity.configura(cartella, da, a, url=None, google=False)
        log("  ! Google ha appena messo in pausa le richieste: XUnity non lo usera' per le frasi nuove")
    dal_vivo, nota = _dal_vivo(cartella, profilo, locale)
    log("  Frasi nuove mentre giochi: %s" % DAL_VIVO.get(dal_vivo, dal_vivo))
    azioni = {"senza_traduttore": ["installa_locale"], "plugin_nessuno": ["crea_rapporto"]}.get(nota, [])
    incompleta = ("%d frasi non sono pre-tradotte: %s." % (falliti, "verranno tradotte mentre giochi"
                                                           if dal_vivo in ("locale", "online", "google") else
                                                           "resteranno in originale")) if falliti else ""

    def _esito_unity():
        _esito_scrittura(esito, progetto, testi, True, "unity", dal_vivo, incompleta, azioni)
        if esito is not None and nota:
            esito["esito"].extra["dal_vivo_nota"] = nota

    if prova:
        log("  (prova senza rete: le traduzioni finte non vanno nella cache di XUnity)")
        _esito_unity()
        return da
    info = adapter_unity.analizza(cartella)
    if adapter_unity.in_esecuzione(cartella, info["exe"]):
        log("  ! Il gioco e' stato aperto nel frattempo: chiudilo e premi 'Riapplica' (le traduzioni sono gia' salvate).")
        _segna(esito, "GIOCO_APERTO", "Il gioco e' stato aperto mentre lavoravo: BepInEx e XUnity sono installati, ma "
               "le frasi pre-tradotte non sono ancora nel gioco.", motore="unity", scritto=True, dal_vivo=dal_vivo,
               azioni=["chiudi_gioco", "riapplica"])
        return da
    # the fixes made with "Fix translations" (also the re-translations asked there): their lines are written now,
    # with the game closed, in place of the previous ones (see correzioni.percorso_da_scrivere)
    from . import corrections as correzioni
    pendenti = correzioni.da_scrivere(profilo)
    attuali = adapter_unity.voci_cache_xunity(cartella, a) if pendenti else {}
    for t, d in pendenti.items():
        traduzioni[t] = d["valore"]
        presente = attuali.get(t)
        sostituisci.pop(t, None)
        if presente is not None and presente != d["valore"] and d.get("prima") in (None, presente):
            sostituisci[t] = presente  # otherwise: line changed after the fix (by hand in the file?), it stays as is
    conteggi = {}
    esclusi = profilo.get("caratteri_mancanti")  # letters the game's fonts lack: adapted in the cache
    nuove = adapter_unity.scrivi_cache_xunity(cartella, a, traduzioni, log, sostituisci=sostituisci,
                                              conteggi=conteggi, esclusi=esclusi)
    log("  Pre-traduzione: %d frasi aggiunte alla cache di XUnity (gia' presenti: %d)"
        % (nuove, len(traduzioni) - nuove))
    if any(v.get("formato") == "unity-tabelle" for v in progetto.profilo.get("file", [])):
        log("  Tabelle dei testi di Unity Localization, dentro il gioco:")
        progetto.applica()
    scritte = 0
    if pendenti:
        from .characters import adatta_senza
        dopo = adapter_unity.voci_cache_xunity(cartella, a)
        # (a fix with a letter the fonts lack is written adapted: it counts as written that way too)
        scritte = sum(1 for t, d in pendenti.items()
                      if dopo.get(t) in (d["valore"], adatta_senza(d["valore"], esclusi, a) or d["valore"]))
        log("  Correzioni fatte in 'Correggi traduzioni': %d scritte nella cache di XUnity%s" % (
            scritte, ", %d lasciate com'erano (la riga e' stata cambiata dopo la correzione)"
            % (len(pendenti) - scritte) if scritte < len(pendenti) else ""))
        try:
            correzioni.scritte(profilo, list(pendenti))
        except OSError as e:  # already in the cache: next time they are rewritten the same (no harm)
            log("  ! Elenco delle correzioni da scrivere non aggiornato (%s)" % e)
    sostituite = max(0, conteggi.get("sostituite", 0) - sum(1 for t in pendenti if t in sostituisci))
    if cache:
        log("  Cache di XUnity: %d righe corrette con una traduzione nuova%s" % (
            sostituite, ", %d restano com'erano (nessuna traduzione migliore per ora)"
            % (len(cache) - sostituite) if len(cache) > sostituite else ""))
    _esito_unity()
    if esito is not None and sostituite:
        esito["esito"].extra["cache_corrette"] = sostituite
    if esito is not None and scritte:
        esito["esito"].extra["correzioni_scritte"] = scritte
    if falliti:
        log(DETTAGLIO + "  ! Pre-traduzione incompleta: %d frasi %s." % (falliti, "verranno tradotte mentre giochi"
                                                             if dal_vivo != "nessuno" else "restano in originale"))
        _dopo_i_tentativi(esito, log)
        return False
    return da


def scrivi_correzioni_unity(profilo, log=print):
    """Unity, with the game closed: the XUnity cache lines still to write (fixes from "Fix translations" saved with the
    game open, see correzioni.percorso_da_scrivere) written now, without reinstalling anything, with the same rules as
    unity_completo: a line changed after the fix (by hand in the file) stays as it is, the letters the game's fonts
    lack are adapted. Used by the after-play check. Returns (written, how many were waiting)."""
    from . import corrections as correzioni
    from .characters import adatta_senza
    pendenti = correzioni.da_scrivere(profilo)
    if not pendenti:
        return 0, 0
    cartella, a = profilo["cartella_gioco"], profilo["lingua_destinazione"]
    attuali = adapter_unity.voci_cache_xunity(cartella, a)
    traduzioni, sostituisci = {}, {}
    for t, d in pendenti.items():
        traduzioni[t] = d["valore"]
        presente = attuali.get(t)
        if presente is not None and presente != d["valore"] and d.get("prima") in (None, presente):
            sostituisci[t] = presente
    esclusi = profilo.get("caratteri_mancanti")
    adapter_unity.scrivi_cache_xunity(cartella, a, traduzioni, log, sostituisci=sostituisci, esclusi=esclusi)
    dopo = adapter_unity.voci_cache_xunity(cartella, a)
    scritte = sum(1 for t, d in pendenti.items()
                  if dopo.get(t) in (d["valore"], adatta_senza(d["valore"], esclusi, a) or d["valore"]))
    correzioni.scritte(profilo, list(pendenti))
    return scritte, len(pendenti)


def _righe_cache_da_rifare(cartella, a, da, progetto, log):
    """{originale: traduzione nella cache} of the XUnity cache lines to fix (read-only):
    - those that adapter_unity.controlla_cache_xunity reports (identical to the original with several words, in
      another language, different line breaks or placeholders, a translated game name);
    - the provisional answers of the live translator (journal: 'rilassata', 'rifiutata', 'rinviata'), which XUnity
      saved as they were.
    Only those written by PatchSmith: the translation in the cache is the one from the translation memory, the machine
    translations or the journal. Lines changed by hand (or written by others) are never touched."""
    voci = adapter_unity.voci_cache_xunity(cartella, a)
    if not voci or da == lingua.AUTO:
        return {}
    nostre, provvisorie = valori_nostri(progetto.profilo, progetto.memoria, progetto._leggi_automatiche())
    segnalate = adapter_unity.controlla_cache_xunity(voci, da, progetto.profilo["lingua_destinazione"],
                                                     progetto.nomi_attivi(list(voci)))
    for t, risposta in provvisorie.items():
        if voci.get(t) == risposta and t not in segnalate:
            segnalate[t] = (risposta, ["tradotta mentre giocavi senza tutti i controlli"])
    da_rifare = {t: v for t, (v, _) in segnalate.items() if v in nostre.get(t, ())}
    if segnalate:
        esempio = next(iter(da_rifare or segnalate))
        log(DETTAGLIO + "  Cache di XUnity: %d righe da correggere (es. %r: %s); %d le ha scritte PatchSmith e si sostituiscono "
            "con una traduzione nuova, le altre (cambiate a mano o scritte da altri) restano come sono" % (
                len(segnalate), esempio[:50], ", ".join(segnalate[esempio][1]), len(da_rifare)))
    return da_rifare


def valori_nostri(profilo, memoria, automatiche):
    """({originale: {translations written by PatchSmith}}, {originale: provisional answer from the journal}) to
    recognize the XUnity cache lines written by PatchSmith (the others, changed by hand or written by others, are not
    touched): the translation memory, the machine translations, the live translator's answers (journal), also with
    the characters the game's fonts lack already adapted (profilo["caratteri_mancanti"]). Read-only."""
    from .characters import adatta_senza
    from .xunity_service import leggi_diario
    esclusi = set(profilo.get("caratteri_mancanti") or "")
    a = profilo.get("lingua_destinazione") or "it"
    nostre, provvisorie = {}, {}

    def aggiungi(t, v):
        valori = nostre.setdefault(t, set())
        valori.add(v)
        if esclusi:
            adattata = adatta_senza(v, esclusi, a)
            if adattata:
                valori.add(adattata)
    for fonte in (memoria, automatiche):
        for t, v in (fonte or {}).items():
            if isinstance(v, str):
                aggiungi(t, v)
    for riga in leggi_diario(profilo["nome"]):
        risposta = riga.get("risposta")
        if not isinstance(risposta, str):
            continue
        aggiungi(riga["testo"], risposta)
        if riga.get("esito") == "ok":
            provvisorie.pop(riga["testo"], None)  # translated properly later
        else:
            provvisorie[riga["testo"]] = risposta
    return nostre, provvisorie


def _extra_tentativi(progetto):
    """For the outcome: the texts left without a translation even after the new automatic rounds (Progetto._recupera),
    the saved translations redone and the fixes made by hand that cannot be written into the game."""
    extra = {}
    if progetto.ultimi_falliti:
        extra["ritentati"] = len(progetto.ultimi_falliti)
    if progetto.rifatte:
        extra["rifatte"] = progetto.rifatte
    problemi = progetto.problemi()
    a_mano = [t for t, v in problemi.items() if v.get("dove") == "gioco" and not v.get("automatica")]
    if a_mano:
        extra["corrette_a_mano_scartate"] = len(a_mano)
    if getattr(progetto, "accorciate", 0):
        extra["accorciate"] = progetto.accorciate
    if getattr(progetto, "lunghe_annotate", 0):  # (only those of this operation: the others are already in the list)
        extra["troppo_lunghe"] = progetto.lunghe_annotate
    if getattr(progetto, "_adattate_font", 0):
        extra["adattate_font"] = progetto._adattate_font
    return extra


def _dopo_i_tentativi(esito, log):
    """In place of the old 'rilancia' (which repeated the same requests to the translator, with the same answers): what
    PatchSmith has already tried on its own and what is still needed."""
    extra = getattr((esito or {}).get("esito"), "extra", None) or {}
    if extra.get("ritentati"):
        log("  %d testi restano in originale anche dopo i nuovi tentativi automatici (frasi separate, parametri "
            "diversi): li vedi con 'Mostra i rimasti' e alla prossima "
            "'Analizza e traduci' li riprovo con parametri nuovi. Una traduzione salvata che non va bene si corregge "
            "con 'Correggi traduzioni'." % extra["ritentati"])
    if extra.get("corrette_a_mano_scartate"):
        log("  %d traduzioni corrette a mano non si possono scrivere nel gioco (il motivo e' sopra): sistemale con "
            "'Correggi traduzioni'." % extra["corrette_a_mano_scartate"])


def _dal_vivo(cartella, profilo, locale):
    """(who translates the new sentences while the game is played, note) for the outcome. The first is 'locale' or
    'online' (PatchSmith's translator with the local translator, or only with the online ones), 'google' (XUnity on
    its own), 'nessuno', None (XUnity not installed). The note (esito.NOTE_DAL_VIVO) explains the cases to fix:
    'senza_traduttore' (no translator available now), 'plugin_google'/'plugin_nessuno' (the plugin that starts
    PatchSmith's translator could not be prepared)."""
    from . import xunity_service as servizio_xunity
    tipo = adapter_unity.traduttore_dal_vivo(cartella)
    if tipo == "servizio":
        nomi = servizio_xunity.traduttori_previsti(profilo)
        if not nomi:
            return "nessuno", "senza_traduttore"
        return ("locale" if "locale" in nomi else "online"), None
    if locale and tipo in ("google", "nessuno"):  # PatchSmith's translator was wanted, but the plugin is not there
        return tipo, "plugin_" + tipo
    return tipo, None


def file_memoria(cartella, destinazione=None):
    """Path of the game's translations file, without opening it (so it opens even if it is broken)."""
    percorso = trova_profilo(cartella)
    if not percorso:
        return None
    try:
        with open(percorso, encoding="utf-8") as f:
            profilo = json.load(f)
    except (OSError, ValueError):
        return None
    if destinazione:
        profilo = dict(profilo, lingua_destinazione=destinazione)
    if profilo.get("motore") == "unity":
        return adapter_unity.file_traduzioni(profilo["cartella_gioco"], profilo["nome"], profilo["lingua_destinazione"])
    return percorso_memoria(profilo)


# --- checks before starting ------------------------------------------------------------------------------------

# a problem found before a long operation: code, message for the player, proposed action, data
Preliminare = collections.namedtuple("Preliminare", "codice messaggio azione dati")
CODICI_PRELIMINARI = ("GIOCO_APERTO", "LINGUA", "SOLA_LETTURA", "SPAZIO", "TRADUTTORE_IN_PAUSA", "LOCALE_ASSENTE",
                      "STESSO_GIOCO")
SPAZIO_GIOCO = 500 * 1024 * 1024  # free on the game's disk to translate or reapply (backups, BepInEx, pak...)
SPAZIO_DATI = 100 * 1024 * 1024  # free on the disk of PatchSmith's data (translation memory, game profiles, logs)
_COSA = {"traduci": "tradurlo", "riapplica": "riapplicare le traduzioni", "ripristina": "rimettere i file originali"}


def prova_scrittura(cartella):
    """None if the folder can be written to (with a test file, removed right away), otherwise the error (OSError)."""
    prova = os.path.join(cartella, ".patchsmith-prova-scrittura-%d.tmp" % os.getpid())
    try:
        with open(prova, "wb") as f:
            f.write(b"patchsmith")
    except OSError as e:
        return e
    finally:
        try:
            os.remove(prova)
        except OSError:
            pass
    return None


def _in_programmi(cartella):
    """True if the folder is inside C:\\Programmi (Program Files), which Windows protects."""
    for variabile in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        radice = os.environ.get(variabile)
        if radice and _dentro(cartella, radice):
            return True
    return False


def _messaggio_sola_lettura(cartella, errore):
    if not isinstance(errore, PermissionError):
        return ("Non posso scrivere nella cartella del gioco (%s): %s. Controlla che il disco non sia pieno o di sola "
                "lettura, poi riprova." % (cartella, errore.strerror or errore))
    testo = "Non posso scrivere nella cartella del gioco (%s): Windows nega l'accesso.\n\n" % cartella
    if _in_programmi(cartella):
        testo += ("Il gioco è nella cartella Programmi, che Windows protegge. Puoi spostarlo in un'altra cartella (con "
                  "Steam: Proprietà del gioco > File installati > Sposta la cartella di installazione, in una libreria "
                  "fuori da Programmi), oppure chiudere PatchSmith e riaprirlo come amministratore (tasto destro > "
                  "Esegui come amministratore).")
    else:
        testo += ("Di solito la blocca l'\"Accesso controllato alle cartelle\" di Windows (Sicurezza di Windows > "
                  "Protezione da virus e minacce > Protezione ransomware) o l'antivirus. Puoi consentire Python in "
                  "quelle impostazioni, oppure chiudere PatchSmith e riaprirlo come amministratore (tasto destro > "
                  "Esegui come amministratore).")
    return testo + "\n\nNon ho toccato nulla."


def _dimensione(byte):
    return "%.1f GB" % (byte / 1024 ** 3) if byte >= 1024 ** 3 else "%d MB" % (byte // 1024 ** 2)


def _somiglia_a_un_codice(codice):
    return bool(re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,4})?", codice or ""))


def _problemi_lingue(da, a):
    """The problems of the chosen languages (From/To): unknown, a code not in the list, the same language."""
    uscita = []
    for campo, codice in (("Da (lingua del gioco)", da), ("A (lingua della traduzione)", a)):
        if codice is None or codice in lingua.NOMI or (codice == lingua.AUTO and campo.startswith("Da")):
            continue
        if _somiglia_a_un_codice(codice):
            uscita.append(Preliminare(
                "LINGUA", "La lingua in %s è il codice \"%s\", che non è nell'elenco. Se è un codice valido di Google "
                "Translate puoi continuare; altrimenti scegli la lingua dall'elenco." % (campo, codice), "continua",
                {"codice": codice}))
        else:
            uscita.append(Preliminare(
                "LINGUA", "Non riconosco la lingua in %s: \"%s\". Scegli una lingua dall'elenco%s." % (
                    campo, codice, " oppure \"Rileva automaticamente\"" if campo.startswith("Da") else ""), None,
                {"codice": codice}))
    if not uscita and da and a and da != lingua.AUTO and lingua.stessa_lingua(da, a):
        uscita.append(Preliminare(
            "LINGUA", "Da e A sono la stessa lingua (%s): non c'è niente da tradurre. In Da scegli la lingua del gioco, "
            "oppure \"Rileva automaticamente\"." % lingua.etichetta(a), None, {"codice": a}))
    return uscita


def controlli_preliminari(cartella, backend=None, operazione="traduci", da=None, a=None):
    """Quick checks before a long operation (operazione: traduci, riapplica, ripristina), so as not to discover a
    problem after minutes or hours of work. Returns [Preliminare(codice, messaggio, azione, dati)] in the order in
    which to show them (empty: it can start):
    - GIOCO_APERTO (action 'riprova'): a program from the game folder is open;
    - LINGUA (traduci only; None, or 'continua' for a code not in the list): From/To languages unknown or the same;
    - SOLA_LETTURA (None): the game folder cannot be written to (Program Files, controlled access, antivirus);
    - SPAZIO (traduci and riapplica; 'continua'): little space on the game's disk or on the one of PatchSmith's data;
    - TRADUTTORE_IN_PAUSA (traduci only; 'usa_locale' or 'installa_locale'): the chosen online translator is paused
      after a block (Limitatore.pausa);
    - LOCALE_ASSENTE (traduci only; 'installa_locale', or continue: the official language does not need translating,
      and without a translator Analyze and translate stops before modifying the game): the local translator is
      chosen, but it is not installed;
    - STESSO_GIOCO (traduci only, folder without a game profile; 'riusa'): the same game is already translated in
      another folder (copied, moved, reinstalled); dati: what stessi_giochi says.
    Reads only, except for a test file written and removed right away in the game folder. With the test translator
    ('finto') no translator checks."""
    cartella = os.path.abspath(cartella)
    uscita = []
    try:
        percorso = trova_profilo(cartella)
        registrata = _leggi_profilo(percorso).get("cartella_gioco") if percorso else None
    except (OSError, ValueError, AttributeError):
        percorso = registrata = None
    cartelle = list(dict.fromkeys(os.path.abspath(d) for d in (cartella, registrata) if d and os.path.isdir(d)))
    for d in cartelle:
        try:
            aperto = sistema.gioco_aperto(d)
        except sistema.ErroreSistema:
            aperto = False  # unknown: the operation's own checks will tell
        if aperto:
            uscita.append(Preliminare("GIOCO_APERTO", "Il gioco è aperto: chiudilo e premi \"Riprova\". Mentre è aperto "
                                      "non posso %s." % _COSA.get(operazione, "lavorare sui suoi file"), "riprova",
                                      {"cartella": d}))
            break
    if operazione == "traduci":
        uscita += _problemi_lingue(da, a)
    dove = registrata if registrata and os.path.isdir(registrata) else (adapter_unity.trova_radice(cartella) or cartella)
    errore = prova_scrittura(dove)
    if errore is not None:
        uscita.append(Preliminare("SOLA_LETTURA", _messaggio_sola_lettura(dove, errore), None,
                                  {"cartella": dove, "errore": str(errore)}))
    if operazione in ("traduci", "riapplica"):
        for disco, minimo, testo in ((dove, SPAZIO_GIOCO, "Sul disco del gioco (%s) restano solo %s liberi: PatchSmith "
                                      "salva una copia dei file originali e aggiunge i suoi file"),
                                     (cartella_dati(), SPAZIO_DATI, "Sul disco dove PatchSmith salva le traduzioni "
                                      "(%s) restano solo %s liberi")):
            esistente = disco
            while esistente and not os.path.isdir(esistente) and os.path.dirname(esistente) != esistente:
                esistente = os.path.dirname(esistente)  # (the data folder may not exist yet)
            try:
                liberi = shutil.disk_usage(esistente).free
            except (OSError, ValueError):
                continue
            if liberi < minimo:
                unita = os.path.splitdrive(os.path.abspath(esistente))[0] or esistente
                uscita.append(Preliminare("SPAZIO", (testo % (unita, _dimensione(liberi))) + ". Con il disco pieno "
                                          "l'operazione si fermerebbe a metà: libera un po' di spazio e riprova, oppure "
                                          "continua comunque.", "continua", {"cartella": esistente, "liberi": liberi}))
    if operazione == "traduci" and backend not in (None, "finto"):
        from . import ollama
        from .limiter import NOMI, Limitatore
        try:
            locale_pronto = ollama.stato() == "pronto"
        except OSError:
            locale_pronto = False
        if backend in NOMI:
            try:
                pausa = Limitatore(backend, log=None).pausa()
            except (OSError, ValueError, TypeError, AttributeError):
                pausa = None
            if pausa:
                uscita.append(Preliminare(
                    "TRADUTTORE_IN_PAUSA", "%s\n\nIl traduttore locale non ha limiti né blocchi: %s\n\nSe continui con "
                    "%s, uso solo le traduzioni già salvate: i testi nuovi restano in originale." % (
                        pausa, "vuoi usarlo?" if locale_pronto else "si scarica una volta sola e poi traduce anche "
                        "senza internet. Vuoi installarlo?", NOMI[backend]),
                    "usa_locale" if locale_pronto else "installa_locale", {"traduttore": backend}))
        elif backend == "locale" and not locale_pronto:
            uscita.append(Preliminare(
                "LOCALE_ASSENTE", "Hai scelto il traduttore locale, ma non è ancora installato: si scarica una volta "
                "sola (qualche GB: prima ti dico cosa scarica) e poi traduce senza limiti, senza blocchi e anche senza "
                "internet.\n\nSe preferisci un traduttore online, sceglilo nel menu \"Traduttore\". Se continui senza "
                "installarlo analizzo lo stesso il gioco: se ha già la sua traduzione ufficiale basta quella, "
                "altrimenti mi fermo prima di modificare qualcosa.", "installa_locale", {}))
    if operazione == "traduci" and not percorso:
        try:
            simili = stessi_giochi(cartella, a, prova=backend == "finto")
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            simili = []
        if simili:
            s = simili[0]
            dove_era = ("che era in %s (quella cartella non c'è più: il gioco è stato spostato?). Il suo profilo passa "
                        "a questa cartella" % s["cartella"] if s["spostato"] else "in %s" % s["cartella"])
            uscita.append(Preliminare(
                "STESSO_GIOCO", "Questa cartella non ha ancora traduzioni, ma sembra lo stesso gioco di \"%s\", %s, già "
                "tradotto: %d testi salvati.\n\nRiuso le sue traduzioni? Così non lo traduco da capo: traduco solo i "
                "testi che mancano." % (s["nome"], dove_era, s["testi"]), "riusa", s))
    return uscita
