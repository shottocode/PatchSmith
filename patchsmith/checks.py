"""After-play check: what the game left behind since the last time PatchSmith wrote to it, and the safe fixes made
on their own. It starts on its own when PatchSmith opens (for games with new signals), with the "Re-translate"
button (the lines to redo) and from the command line ("check" command).

Signals read (read only):
- Unity: the Crash_* folders in %TEMP%\\<azienda>\\<prodotto>\\Crashes, Player.log and Player-prev.log in
  %USERPROFILE%\\AppData\\LocalLow\\<azienda>\\<prodotto> (company and product from <gioco>_Data/app.info),
  BepInEx/LogOutput.log, the configuration of BepInEx, of XUnity and of PatchSmith's translator, the state written by
  the plugin, the journal of the lines translated while playing (servizio_xunity) and the log of the live translator.
  The LocalLow and %TEMP% folders are the same for all copies of a game: a log counts only if it belongs to THIS
  copy (the game's path written inside it: "Mono path[0]" or "Symbol-SearchPath");
- games with translated files (GameMaker, web, Unity Localization...): the fingerprints of the files
  (.patchsmith-info) and, for GameMaker's added language, the language in the options saved by the game;
- Unreal: PatchSmith's pak and the game's log (%LOCALAPPDATA%\\<Progetto>\\Saved\\Logs); XNA: the satellites;
- the other engines: the crashes in the Windows Application log (events 1000 and 1026 of the game's executable).
Only the signals that arrived after PatchSmith's last write to the game count (installation, Reapply, a fix made by
this check).

Rules for automatic fixes (Problema.sicuro):
- only with the game closed (sistema.gioco_aperto): with the game open they are postponed to the next check;
- only once per signal: every attempt is kept in profiles/<nome>.controllo.json (not <nome>.stato.json, which
  project.py deletes as a file of versions 0.1-0.2); if the problem comes back, it stays "to do";
- reversible: they change only PatchSmith's files (BepInEx and XUnity configuration, plugin, translation cache,
  translation memory) or put back the saved translations (like "Reapply"); "Restore originals" removes everything
  as always;
- never the game's saves;
- every result goes into the log and the outcome (come_esito: esito.Esito with the list of problems).
Fixes that remove the translation (the game crashes even after the fix) or that need a choice (new texts to
translate after an update) do not start on their own: they stay "to do", with the right button.
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import (__version__, adapter_unity, adapter_unreal, adapter_xna, components as componenti,
               shared_file as file_condiviso, interruption as interruzione)
from . import language as lingua, system as sistema
from .adapter_webjson import scrivi_atomico
from .backend import ErroreBackend, crea_backend
from .outcome import Esito
from .project import SUFFISSI_INFO, _contiene_tag, _hash_file, leggi_info, percorso_memoria

SUFFISSO_STATO = ".controllo.json"  # profiles/<nome>.controllo.json (automatico.elenco_profili skips it)
GRAVITA = ("errore", "avviso", "info")
# state of a problem after the check: fixed on its own, fix failed, fix already tried for the same signal (the
# problem came back), postponed (game open), to do (the user is needed), to fix (read only: it would be fixed on its
# own), just to know
STATI = ("trovato", "corretto", "non_riuscito", "gia_tentato", "rimandato", "da_fare", "da_correggere", "info")
MAX_BYTE = 2 * 1024 * 1024  # at most this much of each log is read (the start and the end)
MAX_TESTA = 256 * 1024  # the start of a large log (startup of Unity and BepInEx)
MAX_TENTATIVI_SALVATI = 20  # attempts kept for each fix
MAX_FRASI = 200  # journal lines re-translated per check ("Analyze and translate" retries the others)
GIORNI_EVENTI = 30  # crashes in the Windows log: at most from these last days
SEGNALI_XUNITY = (  # (expression, what it means) in the Unity and BepInEx logs
    (r"consecutive errors occurred\. Shutting down", "spento dopo troppi errori"),
    (r"Failed: '", "frasi non tradotte"),
    (r"SPAM DETECTED", "fermato dal controllo anti-spam di XUnity"),
    (r"GoogleTranslateEndpointV2", "errore di Google"),
)
STACK_MOD = ("BepInEx", "HarmonyX", "Harmony", "XUnity", "PatchSmith", "MonoMod")
DESCRIZIONI_CHIAVI = {  # keys of the XUnity and BepInEx configuration, in words for the player
    "Endpoint": "chi traduce le frasi nuove", "FallbackEndpoint": "chi traduce le frasi nuove",
    "Url": "chi traduce le frasi nuove", "EnableShortDelay": "limiti per le frasi nuove",
    "DisableSpamChecks": "limiti per le frasi nuove", "MaxCharactersPerTranslation": "limiti per le frasi nuove",
    "EnableBatching": "limiti per le frasi nuove", "Language": "lingue", "FromLanguage": "lingue",
    "EnableIMGUI": "testi disegnati dal gioco (IMGUI)", "EnableTextPathLogging": "opzioni di prova di XUnity",
    "EnableTranslationHelper": "opzioni di prova di XUnity", "CopyToClipboard": "opzioni di prova di XUnity",
    "Type": "avvio di BepInEx (Unity 6)", "WriteUnityLog": "registro delle lettere mancanti nei font del gioco",
}
ESITI_PLUGIN = {"cfg-mancante": "manca il suo file di configurazione",
                "patchsmith-mancante": "PatchSmith non si trova piu' dove era",
                "python-mancante": "Python non si trova piu' dove era", "errore": "non e' partito"}


class ErroreControllo(Exception):
    """A fix that cannot be made: the message is for the player."""


@dataclass
class Problema:
    """codice: the type (e.g. CRASH_AVVIO, DAL_VIVO, RIPRISTINATO_DAL_GIOCO). gravita: errore/avviso/info. messaggio:
    a sentence for the player. rimedio: the fix (function without arguments; returns a sentence, (riuscito, frase) or
    (riuscito, frase, action to offer if it is still to do)), or None. sicuro: the fix starts on its own (with the
    game closed, once per signal). segnale: what found it (a crash folder, a log with its date...): the same fix for
    the same signal is not repeated. azione: the interface button if it is still to do (esito.AZIONI). chiave: fixes
    with the same key are made only once (e.g. 'riconfigura' fixes configuration, version and live translator
    together). stato and dettaglio: how it went (STATI)."""
    codice: str
    gravita: str
    messaggio: str
    rimedio: object = None
    sicuro: bool = False
    segnale: str = ""
    azione: str = None
    chiave: str = None
    stato: str = "trovato"
    dettaglio: str = ""
    extra: dict = field(default_factory=dict)

    def come_dizionario(self):
        uscita = {"codice": self.codice, "gravita": self.gravita, "messaggio": self.messaggio, "sicuro": self.sicuro,
                  "segnale": self.segnale, "azione": self.azione, "stato": self.stato, "dettaglio": self.dettaglio}
        if self.extra.get("frasi"):  # lines to re-translate (the "Re-translate N lines" button)
            uscita["frasi"] = self.extra["frasi"]
        return uscita


# --- check state (profiles/<nome>.controllo.json) -----------------------------------------------------------

def percorso_stato(percorso_profilo):
    return os.path.splitext(os.path.abspath(percorso_profilo))[0] + SUFFISSO_STATO


def leggi_stato(percorso_profilo):
    """{ultimo_controllo, visti: {segnale: valore}, rimedi: {chiave: [{segnale, quando, ts, riuscito, dettaglio}]},
    diario_fino, entrypoint_forzato, ultimo: {quando, corretti, da_fare, problemi}}; empty if missing."""
    try:
        with open(percorso_stato(percorso_profilo), encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        return {}
    return dati if isinstance(dati, dict) else {}


def _salva_stato(percorso_profilo, stato):
    p = percorso_stato(percorso_profilo)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    scrivi_atomico(p, json.dumps(stato, ensure_ascii=False, indent=2).encode("utf-8"))


def _adesso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _quando(istante):
    try:
        return datetime.datetime.fromtimestamp(istante).strftime("%d/%m alle %H:%M")
    except (OSError, OverflowError, ValueError, TypeError):
        return "?"


def _mtime(percorso):
    try:
        return os.path.getmtime(percorso)
    except (OSError, TypeError, ValueError):
        return None


def _leggi(percorso, massimo=MAX_BYTE):
    """A log (text), or "" if it cannot be read. If it is larger than 'massimo', the start (MAX_TESTA: that is where
    "Mono path[0]" and "Chainloader startup complete" are) and the end (the last errors and the crash) are read: a
    long play session with many warnings must not look like a crash at startup, nor like a log of another copy of the
    game."""
    try:
        with open(percorso, "rb") as f:
            f.seek(0, os.SEEK_END)
            dimensione = f.tell()
            f.seek(0)
            if dimensione <= massimo:
                dati = f.read()
            else:
                testa = f.read(min(MAX_TESTA, massimo // 2) // 2 * 2)
                inizio = dimensione - (massimo - len(testa))
                f.seek(inizio + inizio % 2)  # (even: UTF-16 logs stay readable)
                a_capo = {b"\xff\xfe": b"\n\x00", b"\xfe\xff": b"\x00\n"}.get(testa[:2], b"\n")
                dati = testa + a_capo + f.read()
    except OSError:
        return ""
    if dati.startswith((b"\xff\xfe", b"\xfe\xff")):
        return dati.decode("utf-16", "replace")
    return dati.decode("utf-8-sig", "replace")


def _stessa(a, b):
    """True if the two paths are the same folder (case, slashes and trailing '/' do not matter)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)).rstrip("\\/") == os.path.normcase(os.path.abspath(b)).rstrip("\\/")


def _dentro(figlio, padre):
    figlio = os.path.normcase(os.path.abspath(figlio))
    padre = os.path.normcase(os.path.abspath(padre)).rstrip("\\/")
    return figlio == padre or figlio.startswith(padre + os.sep)


def _tupla(versione):
    try:
        return tuple(int(x) for x in str(versione).split("."))
    except (TypeError, ValueError):
        return (0,)


# --- context of a check -----------------------------------------------------------------------------------

class _Contesto:
    def __init__(self, percorso, profilo, stato, log, applica, traduttore, ritraduci=True, eventi=None):
        self.percorso = os.path.abspath(percorso)
        self.profilo = profilo
        self.nome = profilo.get("nome") or os.path.splitext(os.path.basename(percorso))[0]
        self.titolo = os.path.basename(os.path.normpath(profilo.get("cartella_gioco") or "")) or self.nome
        self.motore = profilo.get("motore")
        # (a game profile without a folder must not become the current folder)
        self.cartella = os.path.abspath(profilo["cartella_gioco"]) if profilo.get("cartella_gioco") else ""
        self.a = profilo.get("lingua_destinazione") or "it"
        self.stato = stato
        self.log = log
        self.applica = applica
        self.traduttore = traduttore
        self.ritraduci = ritraduci  # re-translate the journal's provisional lines now (see controlla)
        self.eventi = eventi  # Windows crashes already read (None: they are read if needed)
        self._aperto = None
        self.riferimento = 0.0
        self.diario_fino = None  # up to where the live translator's journal has nothing left to redo

    def aperto(self):
        """True if the game is open (when in doubt it is considered open: better to postpone than to risk it)."""
        if self._aperto is None:
            try:
                self._aperto = bool(sistema.gioco_aperto(self.cartella))
            except sistema.ErroreSistema:
                self._aperto = True
        return self._aperto

    def ultimo_rimedio(self):
        tempi = [v.get("ts", 0) for voci in (self.stato.get("rimedi") or {}).values() if isinstance(voci, list)
                 for v in voci if isinstance(v, dict)]
        return max(tempi, default=0.0)


def _leggi_profilo(percorso):
    with open(percorso, encoding="utf-8") as f:
        dati = json.load(f)
    if not isinstance(dati, dict):
        raise ValueError("profilo non valido")
    return dati


# --- Unity: logs and crashes -------------------------------------------------------------------------------

def registri_unity(cartella):
    """{'player': [Player.log, Player-prev.log], 'crash': Crashes folder, 'azienda', 'prodotto'} of the Unity game
    (folders shared by all copies of the game), or {} without app.info."""
    from . import language_unity as lingua_unity
    info = adapter_unity.analizza(cartella)
    if not info.get("dati"):
        return {}
    azienda, prodotto = lingua_unity.azienda_prodotto(info["dati"])
    if not azienda or not prodotto:
        return {}
    utente = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    locallow = os.path.join(utente, "AppData", "LocalLow", azienda, prodotto)
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    return {"player": [os.path.join(locallow, n) for n in ("Player.log", "Player-prev.log")],
            "crash": os.path.join(temp, azienda, prodotto, "Crashes"), "azienda": azienda, "prodotto": prodotto}


def di_questa_copia(testo, cartella):
    """True if the Unity log belongs to this copy of the game, False if to another one, None if unknown. It looks at
    'Mono path[0]' (<gioco>/<exe>_Data/Managed), 'Discovering subsystems at path' (<gioco>/<exe>_Data/
    UnitySubsystems: also in IL2CPP games) or, in crash logs, 'Symbol-SearchPath'."""
    m = re.search(r"^Mono path\[0\] = '([^']*)'", testo, re.M)
    if m:
        managed = m.group(1).replace("/", os.sep)
        return _stessa(os.path.dirname(os.path.dirname(managed)), cartella)
    m = re.search(r"Discovering subsystems at path (.+?)[\\/]UnitySubsystems\s*$", testo, re.M)
    if m:
        return _stessa(os.path.dirname(m.group(1).replace("/", os.sep)), cartella)
    m = re.search(r"Symbol-SearchPath: '([^']*)'", testo)
    if m:
        voci = [v for v in m.group(1).split(";") if v and v != "."]
        if voci:
            return any(_stessa(v, cartella) for v in voci)
    return None


def crash_unity(registri):
    """[(folder name, time, path of the Player.log)] of the Unity crashes (all copies of the game)."""
    cartella = registri.get("crash")
    try:
        nomi = sorted(os.listdir(cartella)) if cartella else []
    except OSError:
        return []
    uscita = []
    for nome in nomi:
        d = os.path.join(cartella, nome)
        if not nome.startswith("Crash_") or not os.path.isdir(d):
            continue
        registro = os.path.join(d, "Player.log")
        istante = _mtime(registro) or _mtime(d)
        if istante:
            uscita.append((nome, istante, registro))
    return uscita


# TextMeshPro writes to the Unity log every character that a font (with its fallback fonts) lacks: in the game a little
# box or a space appears in its place. Old versions: "Character with ASCII value of 232 was not found..."
_FONT_MANCANTE = re.compile(rb"Unicode value \\[uU]([0-9A-Fa-f]{4,8}) was not found in the \[([^\]\r\n]{0,200})\] "
                            rb"font asset")
_FONT_MANCANTE_VECCHIO = re.compile(rb"Character with ASCII value of (\d{1,7}) was not found in the Font Asset")
MAX_MB_REGISTRO_FONT = 256  # missing characters are searched for in at most these MB of a log (the whole file)


def caratteri_mancanti(percorsi):
    """{character: {font names}} of the characters TextMeshPro did not find in the game's fonts (Unity and BepInEx
    logs, read whole in pieces: the warnings can be in the middle of a large log). Read only."""
    trovati = {}
    for percorso in percorsi:
        try:
            with open(percorso, "rb") as f:
                coda, letti = b"", 0
                while letti < MAX_MB_REGISTRO_FONT * 1024 * 1024:
                    pezzo = f.read(16 * 1024 * 1024)
                    if not pezzo:
                        break
                    letti += len(pezzo)
                    dati = coda + pezzo
                    coda = dati[-512:]
                    if b"was not found in the" not in dati:
                        continue
                    for m in _FONT_MANCANTE.finditer(dati):
                        codice, font = int(m.group(1), 16), m.group(2).decode("utf-8", "replace")
                        if 0x20 < codice < 0x110000 and not 0xD800 <= codice <= 0xDFFF and not chr(codice).isspace():
                            trovati.setdefault(chr(codice), set()).add(font)
                    for m in _FONT_MANCANTE_VECCHIO.finditer(dati):
                        codice = int(m.group(1))
                        if 0x20 < codice < 0x110000 and not 0xD800 <= codice <= 0xDFFF:
                            trovati.setdefault(chr(codice), set()).add("")
        except OSError:
            continue
    return trovati


def _registri_player(c):
    """[path] of the Unity Player.log files of this copy of the game written after PatchSmith's last write."""
    uscita = []
    try:
        registri = registri_unity(c.cartella)
    except OSError:
        return uscita
    for p in registri.get("player", []):
        t = _mtime(p)
        if t and t > c.riferimento and di_questa_copia(_leggi(p, MAX_TESTA), c.cartella):
            uscita.append(p)
    return uscita


def _elenco_caratteri(caratteri):
    return ", ".join("«%s»" % ch for ch in sorted(caratteri)[:12]) + (" e altri" if len(caratteri) > 12 else "")


def _verifica_font(c, registri):
    """Letters of the translations that the game's fonts cannot draw (TextMeshPro warnings in the logs): they are
    noted in the game profile (profilo["caratteri_mancanti"]: from then on every translation written to the game
    adapts them, è -> e') and the ones already written are adapted at once: for XUnity the cache lines written by
    PatchSmith (those changed by hand stay), for Unity Localization with "Reapply". Characters that appear only in the
    game's texts (not in the translations) do not count. An unexpected error here (unreadable translation memory or
    cache) only ends up in the log: it must not hide the game's other problems (crashes, configuration)."""
    try:
        return _lettere_mancanti(c, registri)
    except Exception as e:  # noqa: BLE001 - an extra check
        c.log("  ! Controllo delle lettere dei font non riuscito (%s: %s)" % (type(e).__name__, e))
        return []


def _lettere_mancanti(c, registri):
    trovati = caratteri_mancanti(registri)
    if not trovati:
        return []
    gia = set(c.profilo.get("caratteri_mancanti") or "")
    tutti = gia | set(trovati)

    def leggi_memoria(percorso):  # (missing or corrupted: as if empty)
        try:
            dati = file_condiviso.leggi_json(percorso)
        except (OSError, ValueError):
            return {}
        return dati if isinstance(dati, dict) else {}
    memoria = leggi_memoria(percorso_memoria(c.profilo))
    righe, a_mano = {}, 0
    if c.motore == "unity":
        from .auto import valori_nostri
        from .characters import adatta_senza
        _, a = _lingue_unity(c)
        voci = adapter_unity.voci_cache_xunity(c.cartella, a)
        nostre = None
        for t, v in voci.items():
            if t in lingua.ENDONIMI or not any(ch in tutti for ch in v):
                continue
            if nostre is None:
                automatiche = leggi_memoria(percorso_memoria(c.profilo)[:-len(".json")] + ".automatiche.json")
                nostre = valori_nostri(c.profilo, memoria, automatiche)[0]
            nuova = adatta_senza(v, tutti, a)
            if v in nostre.get(t, ()) and nuova and nuova != v:
                righe[t] = (v, nuova)
            else:
                a_mano += 1
        usati = {ch for v in voci.values() for ch in v if ch in trovati}
    else:
        usati = {ch for v in memoria.values() if isinstance(v, str) for ch in v if ch in trovati}
    nuovi = usati - gia
    if not righe and not nuovi:
        return []  # only in the game's texts, or already adapted
    mostrati = nuovi or {ch for v, _ in righe.values() for ch in v if ch in tutti}
    messaggio = ("Nel gioco alcune lettere delle traduzioni non si vedono (al loro posto un quadratino o uno spazio): "
                 "i font del gioco non hanno %s. Le sostituisco nelle frasi tradotte (es. è diventa e') e da ora le "
                 "traduzioni scritte nel gioco le evitano." % _elenco_caratteri(mostrati))
    # whole sentences, singular and plural (pieces cannot be translated into the interface language)
    if a_mano == 1:
        messaggio += (" Una frase con quelle lettere non la posso cambiare da solo (cambiata a mano, o senza una "
                      "lettera simile): correggila con \"Correggi traduzioni\".")
    elif a_mano:
        messaggio += (" %d frasi con quelle lettere non le posso cambiare da solo (cambiate a mano, o senza una "
                      "lettera simile): correggile con \"Correggi traduzioni\"." % a_mano)
    return [Problema("FONT_MANCANTI", "avviso", messaggio, rimedio=lambda: _adatta_font(c, set(trovati), righe),
                     sicuro=True, segnale="font:" + "".join(sorted(mostrati | nuovi)), chiave="font",
                     azione="correggi" if a_mano else "crea_rapporto",
                     dettaglio="font senza questi caratteri: %s" % "; ".join(
                         "%s U+%04X in %s" % (ch, ord(ch), ", ".join(sorted(f for f in trovati[ch] if f)) or "?")
                         for ch in sorted(trovati)[:12]))]


def _adatta_font(c, caratteri, righe):
    """The fix for FONT_MANCANTI: the characters in the game profile, then the translations already written adapted."""
    from . import scan as scansione
    profilo = _leggi_profilo(c.percorso)
    profilo["caratteri_mancanti"] = "".join(sorted(set(profilo.get("caratteri_mancanti") or "") | set(caratteri)))
    scansione.salva_json(c.percorso, profilo)
    c.profilo["caratteri_mancanti"] = profilo["caratteri_mancanti"]
    if c.motore == "unity":
        _, a = _lingue_unity(c)
        conteggi = {}
        adapter_unity.scrivi_cache_xunity(c.cartella, a, {t: nuova for t, (_, nuova) in righe.items()}, c.log,
                                          sostituisci={t: vecchia for t, (vecchia, _) in righe.items()},
                                          conteggi=conteggi)
        n = conteggi.get("sostituite", 0)
        return True, ("una frase della cache adattata ai font del gioco" if n == 1 else
                      "%d frasi della cache adattate ai font del gioco" % n)
    riuscito, testo = _riapplica(c)
    return riuscito, ("traduzioni riscritte senza le lettere che i font non hanno (%s)" % testo) if riuscito else testo


def _crash_windows(c, dal):
    """The game's crashes in the Windows Application log after 'dal' (events already read or read now)."""
    try:
        eventi = c.eventi if c.eventi is not None else eventi_crash(dal)
        return crash_del_gioco(c.cartella, eventi, dal)
    except Exception:  # noqa: BLE001 - the Windows log is just an extra clue
        return []


def _pila(testo):
    """The part of the crash log after 'Crash!!!' (the call stack)."""
    i = testo.find("Crash!!!")
    return testo[i:] if i >= 0 else testo


# --- Unity: the checks -----------------------------------------------------------------------------------

def _lingue_unity(c):
    """(game language, target language) for XUnity: the one written in its configuration, otherwise the game
    profile's (English if it is 'auto')."""
    ini = adapter_unity.leggi_ini(os.path.join(c.cartella, *adapter_unity.CONFIG_XUNITY.split("/")))
    da = (ini.get("General") or {}).get("FromLanguage") or c.profilo.get("lingua_origine") or "en"
    if da == lingua.AUTO:
        da = "en"
    return da, (ini.get("General") or {}).get("Language") or c.a


def _scelta_dal_vivo(profilo):
    from .auto import DAL_VIVO_SCELTE
    return profilo.get("dal_vivo") if profilo.get("dal_vivo") in DAL_VIVO_SCELTE else "servizio"


def _riconfigura(c):
    """Fixes the Unity installation with the current code: missing files (from the components already downloaded),
    translator of new lines as the game profile wants (normally PatchSmith's: plugin and command redone),
    configuration of XUnity and BepInEx, version in the manifest. Like a part of "Reapply", without touching the
    translations."""
    from .auto import _dal_vivo
    from .outcome import DAL_VIVO
    manifesto = adapter_unity.leggi_manifesto(c.cartella)
    if manifesto is None:
        raise ErroreControllo("l'elenco dei file installati da PatchSmith non e' valido")
    info = adapter_unity.analizza(c.cartella)
    mancanti = [rel for rel in manifesto["file"] if not os.path.exists(os.path.join(c.cartella, *rel.split("/")))]
    pacchetti = ["bepinex5_%s" % info.get("architettura"), "xunity_bepinex5"]
    if mancanti and not all(n in componenti.CATALOGO and componenti.disponibile(n) for n in pacchetti):
        raise ErroreControllo("per rimettere i file mancanti servono i componenti da scaricare: premi 'Analizza e "
                              "traduci'")
    scelta = _scelta_dal_vivo(c.profilo)
    locale = c.percorso if scelta == "servizio" else ("nessuno" if scelta == "nessuno" else False)
    da, a = _lingue_unity(c)
    adapter_unity._aggiorna(c.cartella, manifesto, info, da, a, c.log, locale, c.nome)
    if c.stato.get("entrypoint_forzato"):
        adapter_unity.entrypoint_bepinex(c.cartella, None, forza=True)
    tipo, nota = _dal_vivo(c.cartella, c.profilo, locale)
    testo = "frasi nuove mentre giochi: %s" % DAL_VIVO.get(tipo, tipo or "nessuno")
    if nota == "senza_traduttore":  # PatchSmith's translator is in the game, but right now it has no translators
        return False, "il traduttore di PatchSmith e' pronto nel gioco, ma adesso non ha un traduttore disponibile " \
                      "(quello locale non e' installato e quelli online sono in pausa)", "installa_locale"
    if locale and tipo not in ("locale", "online"):
        return False, "non sono riuscito a preparare il traduttore di PatchSmith nel gioco (%s)" % testo
    return True, testo


def _entrypoint(c):
    adapter_unity.entrypoint_bepinex(c.cartella, None, forza=True)
    c.stato["entrypoint_forzato"] = True
    return "BepInEx ora parte quando il gioco e' pronto (al primo MonoBehaviour)"


def _verifica_unity(c):
    problemi = []
    cartella = c.cartella
    manifesto = adapter_unity.leggi_manifesto(cartella)
    if manifesto is None:
        return [Problema("MANIFESTO", "avviso", "Il file con l'elenco di quello che PatchSmith ha installato nel gioco "
                         "non e' valido: per sicurezza non correggo nulla da solo.", azione="crea_rapporto",
                         segnale="manifesto")]
    if manifesto.get("in_corso"):  # as for "Analyze and translate": a half-done installation does not fix itself
        return [Problema("INSTALLAZIONE_INTERROTTA", "errore", "L'installazione della traduzione si e' interrotta a "
                         "meta': premi \"Ripristina originali\" e poi \"Analizza e traduci\" per rifarla.",
                         azione="ripristina", segnale="in_corso")]
    rif = c.riferimento
    registri = registri_unity(cartella)
    percorso_log = os.path.join(cartella, *adapter_unity.LOG_BEPINEX.split("/"))
    t_log = _mtime(percorso_log)
    testo_log = _leggi(percorso_log) if t_log and t_log > rif else ""

    # 1) crashes after PatchSmith's last write (only those of this copy of the game)
    recenti = []
    for nome, istante, registro in crash_unity(registri):
        if istante <= rif:
            continue
        testo = _leggi(registro)
        if di_questa_copia(testo, cartella):
            recenti.append((istante, nome, testo))
    incompleto = bool(testo_log.strip()) and "Chainloader startup complete" not in testo_log and not c.aperto()
    # a real crash besides the incomplete log: also the Windows Application log (events 1000 and 1026)
    windows = _crash_windows(c, rif) if incompleto and not recenti else []
    if recenti or incompleto:
        recente = max(recenti) if recenti else None
        testo = recente[2] if recente else testo_log
        istante = recente[0] if recente else (max(e["istante"] for e in windows) if windows else t_log)
        quando = _quando(istante)
        volte = "%d volte, l'ultima il %s" % (len(recenti), quando) if len(recenti) > 1 else "il %s" % quando
        firma = "Chainloader:Start" in testo and "Application:.cctor" in testo
        segnale = "crash:%s" % recente[1] if recente else "bepinex:%d" % int(t_log)
        forzato = bool(c.stato.get("entrypoint_forzato"))  # already fixed once by a check
        if (firma or not recente) and adapter_unity.entrypoint_attuale(cartella) != "MonoBehaviour":
            problemi.append(Problema(
                "CRASH_AVVIO", "errore", "Il gioco si e' chiuso all'avvio (%s) mentre partiva BepInEx, il programma che "
                "carica la traduzione: succede con alcuni giochi Unity. Lo faccio partire piu' tardi, quando il gioco "
                "e' pronto." % volte, rimedio=lambda: _entrypoint(c), sicuro=True, segnale=segnale,
                azione="ripristina"))
        elif (recente and (firma or any(k in _pila(testo) for k in STACK_MOD))) or windows:
            # "does not accept mods" only with a real crash (Unity crash folder or Windows log) while BepInEx was
            # starting or with BepInEx in the stack: an incomplete log alone is not enough
            problemi.append(Problema(
                "CRASH_CON_MOD", "errore", "Il gioco si e' chiuso all'improvviso (%s) e la causa sembra BepInEx, il "
                "programma che carica la traduzione%s: questo gioco probabilmente non accetta mod. Premi \"Ripristina "
                "originali\" per giocare senza traduzione (le traduzioni restano salvate) e crea un rapporto." % (
                    volte, ", anche dopo la correzione che lo fa partire piu' tardi" if forzato else ""),
                rimedio=lambda: _togli_unity(c), sicuro=False, segnale=segnale, azione="ripristina"))
        elif recente:
            problemi.append(Problema(
                "CRASH", "avviso", "Il gioco si e' chiuso all'improvviso (%s), ma non sembra colpa della traduzione. Se "
                "succede ancora, prova \"Ripristina originali\" per vedere se senza traduzione va." % volte,
                segnale=segnale, azione="ripristina"))
        else:
            # BepInEx already starts at the first MonoBehaviour and there is no trace of a crash: usually the game was
            # closed while loading. Just to know
            problemi.append(Problema(
                "AVVIO_INCOMPLETO", "info", "All'ultima partita (%s) BepInEx, il programma che carica la traduzione, "
                "non ha finito di partire, ma non c'e' traccia di un crash: forse il gioco e' stato chiuso mentre "
                "caricava. Se invece si chiude da solo all'avvio, premi \"Ripristina originali\" e crea un rapporto."
                % quando, segnale=segnale))

    # 2) the translator of new lines stopped during the play session (XUnity: errors, anti-spam, Google)
    fonti = []
    for registro in registri.get("player", []):
        t = _mtime(registro)
        if t and t > rif:
            testo = _leggi(registro)
            if di_questa_copia(testo, cartella):
                fonti.append((registro, t, testo))
    if testo_log:
        fonti.append((percorso_log, t_log, testo_log))
    trovati, falliti, segnali = set(), [], []
    for registro, t, testo in fonti:
        for espressione, significato in SEGNALI_XUNITY:
            if re.search(espressione, testo):
                trovati.add(significato)
                segnali.append("%s@%d" % (os.path.basename(registro), int(t)))
        falliti += re.findall(r"Failed: '((?:[^'\\]|\\.){1,80})'", testo)
    if trovati:
        tipo = adapter_unity.traduttore_dal_vivo(cartella)
        quanti = ((" (una frase rimasta in originale: \"%s\")" % falliti[0]) if len(set(falliti)) == 1 else
                  (" (%d frasi rimaste in originale, es. \"%s\")" % (len(set(falliti)), falliti[0]))) if falliti else ""
        segnale = "xunity:" + ",".join(sorted(set(segnali)))
        if tipo != "servizio" or "errore di Google" in trovati:
            problemi.append(Problema(
                "TRADUTTORE_FERMO", "avviso", "Durante l'ultima partita il traduttore delle frasi nuove si e' fermato "
                "(%s)%s. Da ora le frasi nuove le traduce PatchSmith, con i suoi traduttori e le loro pause."
                % (", ".join(sorted(trovati)), quanti), rimedio=lambda: _riconfigura(c), sicuro=True, segnale=segnale,
                chiave="riconfigura", azione="crea_rapporto"))
        else:
            from . import xunity_service as servizio_xunity
            nessuno = not servizio_xunity.traduttori_previsti(c.profilo)
            problemi.append(Problema(
                "TRADUTTORE_FERMO", "avviso", "Durante l'ultima partita il traduttore di PatchSmith non ha tradotto "
                "alcune frasi nuove (%s)%s.%s" % (", ".join(sorted(trovati)), quanti,
                                                  " Adesso non c'e' un traduttore disponibile: installa il traduttore "
                                                  "locale." if nessuno else " Alla prossima \"Analizza e traduci\" le "
                                                  "riprovo."),
                segnale=segnale, azione="installa_locale" if nessuno else None))

    # 2b) letters of the translations that the game's fonts cannot draw (TextMeshPro warnings)
    problemi += _verifica_font(c, [registro for registro, _, _ in fonti])

    # 3) installed files that are missing (e.g. removed by the antivirus)
    mancanti = [rel for rel in manifesto["file"] if not os.path.exists(os.path.join(cartella, *rel.split("/")))]
    if mancanti:
        problemi.append(Problema(
            "FILE_MANCANTI", "errore", "Mancano %d file della traduzione nel gioco (es. %s): forse li ha tolti "
            "l'antivirus. Li rimetto." % (len(mancanti), mancanti[0]), rimedio=lambda: _riconfigura(c), sicuro=True,
            segnale="mancanti:%d:%s" % (len(mancanti), mancanti[0]), chiave="riconfigura", azione="riapplica"))

    # 4) PatchSmith's translator cannot start with the game (changed paths, state written by the plugin)
    guasti = []
    if adapter_unity.servizio_attivo(cartella):
        cfg = adapter_unity.leggi_comando_servizio(cartella)
        programma = cfg.get("Programma")
        if not programma or not os.path.exists(programma):
            guasti.append("Python non si trova piu' dove era")
        m = re.search(r'-I "([^"]+)"', cfg.get("Argomenti", ""))
        if not m or not os.path.exists(m.group(1)):
            guasti.append("PatchSmith non si trova piu' dove era")
        m = re.search(r'--profilo "([^"]+)"', cfg.get("Argomenti", ""))
        if not m or not _stessa(m.group(1), c.percorso):
            guasti.append("il profilo del gioco e' cambiato")
    stato_plugin = adapter_unity.leggi_stato_servizio(cartella)
    t_plugin = _mtime(os.path.join(cartella, *adapter_unity.STATO_SERVIZIO.split("/")))
    if stato_plugin and t_plugin and t_plugin > rif and stato_plugin.get("Esito", "avviato") != "avviato":
        guasti.append("all'ultimo avvio del gioco %s" % ESITI_PLUGIN.get(stato_plugin.get("Esito"), "non e' partito"))
    if guasti:
        problemi.append(Problema(
            "SERVIZIO", "avviso", "Il traduttore di PatchSmith per le frasi nuove non puo' partire con il gioco (%s): "
            "lo preparo di nuovo." % "; ".join(guasti), rimedio=lambda: _riconfigura(c), sicuro=True,
            segnale="servizio:" + "|".join(guasti), chiave="riconfigura", azione="crea_rapporto"))

    # 5) who translates new lines, configuration and the version of PatchSmith that wrote it
    problemi += _verifica_configurazione(c, manifesto)

    # 6) lines translated while playing: those with a provisional answer are re-translated, lost ones are saved
    problemi += _verifica_diario(c)

    # 6b) fixes from "Fix translations" saved with the game open and not yet written to the cache
    problemi += _verifica_correzioni(c)

    # 7) the live translator did not start or had no translators (service log)
    problemi += _verifica_registro_servizio(c)
    return problemi


def _verifica_configurazione(c, manifesto):
    problemi = []
    cartella = c.cartella
    scelta = _scelta_dal_vivo(c.profilo)
    tipo = adapter_unity.traduttore_dal_vivo(cartella)
    google = adapter_unity.google_libero() and scelta != "nessuno"
    da, a = _lingue_unity(c)
    url = adapter_unity.url_servizio(cartella) if scelta == "servizio" else None
    attese = adapter_unity._valori_config(da, a, adapter_unity.usa_imgui(cartella), url, google)
    ini = adapter_unity.leggi_ini(os.path.join(cartella, *adapter_unity.CONFIG_XUNITY.split("/")))
    diverse = [(sez, k, (ini.get(sez) or {}).get(k), v) for sez, chiavi in attese.items() for k, v in chiavi.items()
               if (ini.get(sez) or {}).get(k) != v]
    sbagliato = (scelta == "servizio" and tipo != "servizio") or (scelta == "google" and tipo == "servizio") or \
        (scelta == "nessuno" and tipo not in ("nessuno", None))
    if sbagliato:
        if scelta == "nessuno":
            dove = "le traduce ancora un traduttore, ma l'hai spento alla fine di 'Analizza e traduci'"
        elif tipo == "google":
            dove = "le chiede XUnity a Google da solo, senza le pause di PatchSmith"
            from .limiter import Limitatore, quando
            try:
                blocco = Limitatore("google", log=None).blocco_attuale()
            except (OSError, ValueError, TypeError, AttributeError):
                blocco = None
            if blocco:
                dove += ", ma Google e' in pausa (bloccato) %s: restano in originale" % (
                    "finche' non lo riattivi" if blocco.get("disattivato") else "fino " + quando(blocco["fino"]))
        else:
            dove = "nessuno le traduce (restano in originale)"
        problemi.append(Problema(
            "DAL_VIVO", "avviso", "Frasi nuove mentre giochi: %s. %s" % (
                dove, "Le passo al traduttore di PatchSmith." if scelta == "servizio" else
                "Spengo il traduttore, come hai scelto." if scelta == "nessuno" else
                "Le rimetto a Google, come chiede il profilo del gioco."),
            rimedio=lambda: _riconfigura(c), sicuro=True, segnale="dal_vivo:%s" % tipo, chiave="riconfigura",
            azione="crea_rapporto"))
        diverse = [d for d in diverse if d[0] not in ("Service", "Custom") and d[1] != "MaxCharactersPerTranslation"]
    versione_unity = manifesto.get("versione_unity") or adapter_unity.analizza(cartella).get("versione_unity")
    try:
        unity6 = int(str(versione_unity or "").split(".")[0]) >= 6000
    except ValueError:
        unity6 = False
    ingresso = adapter_unity.entrypoint_attuale(cartella)
    if (unity6 or c.stato.get("entrypoint_forzato")) and ingresso != "MonoBehaviour":
        diverse.append(("Preloader.Entrypoint", "Type", ingresso, "MonoBehaviour"))
    if not adapter_unity.registro_unity_attivo(cartella):  # (older installations: _aggiorna turns it on)
        diverse.append(("Logging.Disk", "WriteUnityLog", (adapter_unity.leggi_ini(os.path.join(
            cartella, *adapter_unity.CONFIG_BEPINEX.split("/"))).get("Logging.Disk") or {}).get("WriteUnityLog"),
            "true"))
    versione = manifesto.get("patchsmith_versione")
    # another version, not only a previous one: with the first release the numbering restarted from 0.1.0 (the
    # development versions went up to 0.19.0), so a higher number can belong to an older version
    diversa = _tupla(versione) != _tupla(__version__)
    if diverse:
        cosa = list(dict.fromkeys(DESCRIZIONI_CHIAVI.get(k, k) for _, k, _, _ in diverse))
        # installed with another version: it is said here (the same fix also updates the version in the manifest),
        # without a second VERSIONE message for the same thing
        problemi.append(Problema(
            "CONFIGURAZIONE", "avviso", "La configurazione della traduzione nel gioco non e' quella giusta (%s)%s: la "
            "rimetto a posto." % (", ".join(cosa), ", perche' e' stata installata con un'altra versione di "
                                  "PatchSmith%s" % (" (%s)" % versione if versione else "") if diversa else ""),
            rimedio=lambda: _riconfigura(c), sicuro=True,
            segnale="config:" + "|".join(sorted("%s.%s=%s" % (s, k, v) for s, k, v, _ in diverse)),
            chiave="riconfigura", azione="crea_rapporto",
            dettaglio="; ".join("%s.%s: %s invece di %s" % (s, k, v, att) for s, k, v, att in diverse)))
    elif diversa:
        problemi.append(Problema(
            "VERSIONE", "info", "La traduzione e' stata installata con un'altra versione di PatchSmith%s: "
            "aggiorno la configurazione nel gioco con quella di adesso." % (" (%s)" % versione if versione else ""),
            rimedio=lambda: _riconfigura(c), sicuro=True, segnale="versione:%s" % (versione or "-"),
            chiave="riconfigura", azione="riapplica"))
    return problemi


def _verifica_diario(c):
    """The lines requested while playing (journal of the live translator) after the last check: those with a
    provisional answer (relaxed, refused, deferred) or without an answer (error, interrupted) are re-translated now
    and put into the XUnity cache (a provisional line is replaced only if it still has the journal's answer);
    those translated but missing from the translation memory (lost save) are put back."""
    from .xunity_service import leggi_diario
    fino = c.stato.get("diario_fino") or ""
    righe = [r for r in leggi_diario(c.nome) if str(r.get("ts", "")) > fino]
    if not righe:
        return []
    fino_a = max(str(r.get("ts", "")) for r in righe)
    def leggi_memoria(percorso):  # (missing or corrupted: as if empty, Progetto fixes it when it saves it)
        try:
            dati = file_condiviso.leggi_json(percorso)
        except (OSError, ValueError):
            return {}
        return dati if isinstance(dati, dict) else {}

    memoria = leggi_memoria(percorso_memoria(c.profilo))
    automatiche = leggi_memoria(percorso_memoria(c.profilo)[:-len(".json")] + ".automatiche.json")
    perse, provvisorie = {}, {}
    from .project import _da_tradurre
    for r in righe:
        t = r["testo"]
        if not _da_tradurre(t):  # only numbers, symbols and units ("$2,33k"): they stay as they are, nothing to redo
            continue
        if r.get("esito") == "ok":
            provvisorie.pop(t, None)  # translated properly later
            # (not the pieces already in Italian that the service left as they were: they are not translations)
            if r.get("backend") not in ("memoria", "comune", "gia' tradotta", None) and \
                    isinstance(r.get("risposta"), str) and \
                    t not in memoria and t not in automatiche:
                perse[t] = r["risposta"]
        elif not (isinstance(memoria.get(t), str) and memoria.get(t)):
            provvisorie[t] = r.get("risposta")
    for t in perse:
        provvisorie.pop(t, None)
    if not perse and not provvisorie:
        c.diario_fino = fino_a  # nothing to redo: next time it starts from here
        return []
    if provvisorie and not c.ritraduci:
        # check at startup: re-translating them would mean loading the local translator or waiting for Google's
        # pauses with the window frozen. They stay to do, with the "Re-translate" button (or the next "Analyze and
        # translate"); the journal does not move forward, so the button's check finds them again. Lost lines are
        # saved at once (no translator needed)
        problemi = [Problema("FRASI_DA_RITRADURRE", "avviso", "Mentre giocavi, %d frasi sono rimaste in originale o "
                             "tradotte senza tutti i controlli (es. \"%s\"): premi \"Ritraduci\" per tradurle adesso "
                             "(le riprova anche la prossima \"Analizza e traduci\")." % (
                                 len(provvisorie), " ".join(next(iter(provvisorie)).split())[:60]),
                             azione="ritraduci", segnale="diario:%s" % fino_a, extra={"frasi": len(provvisorie)})]
        if perse:
            problemi.append(Problema("FRASI_DA_RIFARE", "info", "Mentre giocavi, %d frasi tradotte non erano state "
                                     "salvate: le salvo." % len(perse), rimedio=lambda: _rifai_frasi(c, {}, perse),
                                     sicuro=True, segnale="diario-perse:%s" % fino_a, chiave="FRASI_PERSE"))
        return problemi
    parti = []
    if provvisorie:
        parti.append("%d frasi sono rimaste in originale o tradotte senza tutti i controlli (es. \"%s\"): le traduco "
                     "di nuovo adesso" % (len(provvisorie), " ".join(next(iter(provvisorie)).split())[:60]))
    if perse:
        parti.append("%d frasi tradotte non erano state salvate: le salvo" % len(perse))
    return [Problema("FRASI_DA_RIFARE", "info", "Mentre giocavi, %s." % "; ".join(parti),
                     rimedio=lambda: _rifai_frasi(c, provvisorie, perse), sicuro=True,
                     segnale="diario:%s" % fino_a, azione="traduci", extra={"diario_fino": fino_a})]


def _verifica_correzioni(c):
    """Unity: the fixes made in "Fix translations" while the game was open wait in
    memory/<gioco>.<lingua>.da_scrivere.json (the XUnity cache lines are changed only with the game closed) until
    "Reapply" writes them: if nobody pressed it, the check writes them (with the game closed, once)."""
    from . import corrections as correzioni
    try:
        pendenti = correzioni.da_scrivere(c.profilo)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not pendenti:
        return []
    n = len(pendenti)
    impronta = _sha1(json.dumps(sorted((t, d["valore"]) for t, d in pendenti.items()),
                                ensure_ascii=False).encode("utf-8"))[:12]

    def scrivi():
        from .auto import scrivi_correzioni_unity
        scritte, quante = scrivi_correzioni_unity(c.profilo, c.log)
        testo = "%s nel gioco" % ("scritta" if scritte == 1 else "scritte %d" % scritte)
        if scritte < quante:
            testo += (", una lasciata com'era (la riga era stata cambiata dopo la correzione)" if quante - scritte == 1
                      else ", %d lasciate com'erano (le righe erano state cambiate dopo la correzione)"
                      % (quante - scritte))
        return True, testo

    return [Problema(
        "CORREZIONI_IN_ATTESA", "info",
        "Una tua correzione fatta in \"Correggi traduzioni\" con il gioco aperto non era ancora nel gioco: la scrivo "
        "adesso." if n == 1 else "%d tue correzioni fatte in \"Correggi traduzioni\" con il gioco aperto non erano "
        "ancora nel gioco: le scrivo adesso." % n,
        rimedio=scrivi, sicuro=True, segnale="correzioni:%s" % impronta, chiave="correzioni", azione="riapplica")]


def _rifai_frasi(c, provvisorie, perse):
    from .project import Progetto
    progetto = Progetto(c.percorso, c.log)
    if perse:
        progetto.memoria.update(perse)
        progetto._salva_memoria()
        progetto._aggiorna_automatiche(list(perse))
    da, a = _lingue_unity(c)
    testi = list(provvisorie)[:MAX_FRASI]
    falliti, assente = 0, None
    if testi:
        nome = c.traduttore or _traduttore_del_profilo(c.profilo)
        try:
            traduttore = crea_backend(nome, c.log)
        except ErroreBackend as e:  # (e.g. local translator chosen but not installed) lost lines are saved
            # anyway; the others are left to the next "Analyze and translate", with the button to fix it
            assente, testi = e, []
        else:
            falliti = progetto.traduci_testi(testi, traduttore, da, a)
    traduzioni = {t: progetto.memoria[t] for t in list(perse) + testi
                  if isinstance(progetto.memoria.get(t), str) and progetto.memoria[t]}
    voci = adapter_unity.voci_cache_xunity(c.cartella, a)
    nomi = progetto.nomi_attivi(list(traduzioni))
    sostituisci = {t: voci[t] for t in traduzioni if t in voci and voci[t] == provvisorie.get(t)
                   and voci[t] != traduzioni[t] and not adapter_unity.controlla_cache_xunity({t: traduzioni[t]}, da,
                                                                                             a, nomi)}
    conteggi = {}
    adapter_unity.scrivi_cache_xunity(c.cartella, a, traduzioni, c.log, sostituisci=sostituisci, conteggi=conteggi,
                                      esclusi=c.profilo.get("caratteri_mancanti"))
    rimaste = len(provvisorie) - len([t for t in testi if t in traduzioni])
    testo = "%d frasi tradotte e messe nel gioco (%d righe provvisorie sostituite)" % (
        len(traduzioni), conteggi.get("sostituite", 0))
    if rimaste or falliti:
        testo += "; %d restano in originale (\"Analizza e traduci\" le riprova con parametri nuovi)" % max(rimaste,
                                                                                                            falliti)
    if assente is not None:
        testo += ". %s" % assente
        if getattr(assente, "codice", None) == "TRADUTTORE_ASSENTE":
            return False, testo, "installa_locale"
    return (not rimaste), testo


def _traduttore_del_profilo(profilo):
    from .backend import BACKEND, predefinito
    nome = profilo.get("backend")
    if nome in (None, "xunity-google"):
        nome = "google"
    return nome if nome in BACKEND and nome != "finto" else predefinito()


def _verifica_registro_servizio(c):
    """The sessions of this game's live translator after the last write (servizio-xunity.log log, shared by all
    games): it counts if it did not start (port in use) or had no translators ready."""
    from . import ollama, xunity_service as servizio_xunity
    testo = _leggi(os.path.join(ollama.cartella(), "servizio-xunity.log"), 512 * 1024)
    if not testo:
        return []
    porta = "porta %d " % servizio_xunity.porta_per(c.nome)
    corrente, trovati = None, []
    for riga in testo.splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$", riga)
        if not m:
            continue
        messaggio = m.group(2).strip()
        g = re.search(r"\(gioco: ([^)]+)\)\s*$", messaggio)
        if g:
            corrente = g.group(1)
            continue
        # the "! ..." lines (port in use, a translator that does not start) and the translator queue left empty
        senza = messaggio.startswith("Traduttori per le frasi nuove: nessuno")
        if not messaggio.startswith("!") and not senza:
            continue
        nostro = corrente == c.nome or porta in messaggio
        if nostro and (senza or re.search(r"occupata|non pronti|non disponibile|non avviato", messaggio)):
            try:
                istante = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except (ValueError, OverflowError):
                continue
            if istante > c.riferimento:
                trovati.append((istante, messaggio.lstrip("! ").strip()))
    if not trovati:
        return []
    return [Problema("SERVIZIO_REGISTRO", "avviso", "Durante l'ultima partita il traduttore delle frasi nuove ha avuto "
                     "un problema (%s: \"%s\")." % (_quando(trovati[-1][0]), trovati[-1][1][:120]),
                     segnale="registro:%d" % int(trovati[-1][0]), azione="crea_rapporto")]


def _togli_unity(c):
    from . import auto as automatico
    d = {}
    automatico.ripristina(c.cartella, c.log, esito=d)
    e = d.get("esito")
    return (e is not None and e.codice == "OK"), (e.motivo if e is not None else "")


# --- games with translated files, Unreal, XNA ---------------------------------------------------------------

def _riapplica(c, forza_lingua=False):
    """Puts back the saved translations (like "Reapply", without network)."""
    from . import auto as automatico
    d = {}
    automatico.riapplica(c.cartella, c.log, esito=d)
    e = d.get("esito")
    if forza_lingua:
        _rimetti_lingua_aggiunta(c, forza=True)
    if e is None:
        return False, "nessun esito"
    riuscito = e.codice in ("OK", "PARZIALE") and (e.scritto or e.tradotti)
    testo = "traduzioni rimesse nel gioco (testi tradotti: %d%s)" % (
        e.tradotti, ", rimasti in originale: %d" % e.rimasti if e.rimasti else "") if riuscito else \
        (e.motivo or "non riuscito")
    return riuscito, testo


def _verifica_web_vivo(c):
    """Live translation (adapter_web_vivo): if the game put back its own preload.js (update, Steam file
    verification) the script is gone: it is put back (Reapply, without network)."""
    from . import adapter_web_live as adapter_web_vivo
    if adapter_web_vivo.installato(c.cartella):
        return []
    preload = adapter_web_vivo.trova_preload(c.cartella)
    return [Problema("RIPRISTINATO_DAL_GIOCO", "avviso", "Il gioco ha rimesso il suo file originale (preload.js): "
                     "forse un aggiornamento o la verifica dei file di Steam. Rimetto la traduzione dal vivo.",
                     rimedio=lambda: _riapplica(c), sicuro=True,
                     segnale="ripristinato:%s" % (_mtime(preload) if preload else ""), chiave="riapplica",
                     azione="riapplica")]


def _verifica_file(c):
    """Games with translated files: the game (an update, Steam's "Verify integrity") put back its originals or
    changed them; the added language is gone."""
    problemi, rimessi, cambiati, tolte = [], [], [], []
    relativi, cataloghi, impronte = [], set(), {}
    for voce in c.profilo.get("file") or []:
        if isinstance(voce, dict):
            # Unity Localization: also the catalog from which PatchSmith removed the CRC (if the game puts it back,
            # the translated bundle would no longer load)
            relativi += [r for r in (voce.get("percorso"), voce.get("catalogo")) if r and r not in relativi]
            if voce.get("catalogo"):
                cataloghi.add(voce["catalogo"])
    for rel in relativi:
        p = os.path.join(c.cartella, *rel.split("/"))
        info = leggi_info(p)
        if not info or info.get("configuratore"):
            continue
        for nome in info.get("creati", []):  # added language (e.g. local/italiano.json) removed by the game
            if not os.path.exists(os.path.join(os.path.dirname(p), nome)):
                tolte.append(nome)
        if not os.path.exists(p):
            continue
        try:
            attuale = impronte[rel] = _hash_file(p)
        except OSError:
            continue
        if info.get("solo_creati"):
            if attuale != info["hash_originale"]:
                cambiati.append(rel)
        elif attuale == info["hash_originale"]:
            rimessi.append((rel, attuale))
        elif attuale not in info.get("hash_scritti", []) and not _contiene_tag(p):
            cambiati.append(rel)
    if rimessi or tolte:
        nomi = [r for r, _ in rimessi] + tolte
        problemi.append(Problema(
            "RIPRISTINATO_DAL_GIOCO", "avviso", "Il gioco ha rimesso i suoi file originali (%s): forse un "
            "aggiornamento o la verifica dei file di Steam. Rimetto le traduzioni salvate." % ", ".join(nomi[:3]),
            rimedio=lambda: _riapplica(c, forza_lingua=bool(tolte)), sicuro=True,
            segnale="ripristinato:" + "|".join(sorted("%s=%s" % x for x in rimessi) + sorted(tolte)),
            chiave="riapplica", azione="riapplica"))
    nuovi_cataloghi = [r for r in cambiati if r in cataloghi]
    if nuovi_cataloghi and len(nuovi_cataloghi) == len(cambiati):
        # an update changed only the bundle catalog (not the text tables): the new one has the CRC of the original
        # table again and the game would not load the translated one (missing texts). Reapply removes the CRC
        # from the new catalog (which becomes the original, with its backup).
        problemi.append(Problema(
            "RIPRISTINATO_DAL_GIOCO", "avviso", "Un aggiornamento del gioco ha cambiato l'elenco dei suoi file (%s) e "
            "ha rimesso il controllo che impedisce di caricare i testi tradotti. Lo tolgo di nuovo e rimetto le "
            "traduzioni salvate." % ", ".join(nuovi_cataloghi), rimedio=lambda: _riapplica(c), sicuro=True,
            segnale="catalogo:" + "|".join(sorted("%s=%s" % (r, impronte[r]) for r in nuovi_cataloghi)),
            chiave="riapplica", azione="riapplica"))
        cambiati = []
    if cambiati:
        problemi.append(Problema(
            "AGGIORNATO", "avviso", "Dopo la traduzione il gioco ha cambiato %d file con i testi (es. %s): forse un "
            "aggiornamento. Premi \"Analizza e traduci\" per tradurre anche i testi nuovi (quelli gia' tradotti "
            "restano)." % (len(cambiati), cambiati[0]), segnale="cambiati:" + "|".join(sorted(cambiati)),
            azione="traduci"))
    if c.motore == "gamemaker" and not tolte:
        problemi += _verifica_lingua_aggiunta(c)
    return problemi


def _lingua_aggiunta(c):
    """(name of the added language written by PatchSmith in the options, game languages) or (None, None)."""
    from . import auto as automatico, official_language as lingua_ufficiale
    voci = [v for v in c.profilo.get("file") or [] if isinstance(v, dict) and v.get("lingua_nuova")]
    registro = lingua_ufficiale._leggi_registro(c.cartella) or {}
    if not voci or registro.get("tipo") != "gamemaker_opzioni" or not registro.get("scritto"):
        return None, None
    lingue = automatico.lingue_della_cartella(c.cartella, voci[0]["percorso"]) | {registro["scritto"]}
    return registro["scritto"], lingue


def _verifica_lingua_aggiunta(c):
    """GameMaker with the translation as an added language: another language is chosen in the game's options. It is
    unknown whether the player chose it: it says how to set it back, without touching anything."""
    from . import language_gamemaker as lingua_gamemaker
    scritta, lingue = _lingua_aggiunta(c)
    if not scritta:
        return []
    try:
        percorso, posto = lingua_gamemaker.cerca(c.cartella, lingue)
    except OSError:
        return []
    if percorso is None or posto[3] == scritta:
        return []
    return [Problema("LINGUA_AGGIUNTA", "info", "Nel gioco e' scelta la lingua '%s': la traduzione e' la lingua '%s'. "
                     "Se la vuoi, sceglila nel gioco in Opzioni > Lingua." % (posto[3], scritta),
                     segnale="lingua:%s:%s" % (posto[3], int(_mtime(percorso) or 0)))]


def _rimetti_lingua_aggiunta(c, forza=False):
    from . import official_language as lingua_ufficiale
    scritta, lingue = _lingua_aggiunta(c)
    if scritta:
        lingua_ufficiale.imposta_lingua_aggiunta(c.cartella, scritta, lingue, c.log, forza=forza)


def _verifica_unreal(c):
    problemi = []
    manifesto = adapter_unreal.leggi_manifesto(c.cartella)
    if not manifesto:
        return []
    tolti = [rel for rel in manifesto["file"] if not os.path.exists(os.path.join(c.cartella, *rel.split("/")))]
    if tolti:
        problemi.append(Problema(
            "TRADUZIONE_TOLTA", "avviso", "Il file con la traduzione (%s) non c'e' piu' nel gioco: forse un "
            "aggiornamento. Lo rimetto con le traduzioni salvate." % os.path.basename(tolti[0]),
            rimedio=lambda: _riapplica(c), sicuro=True, segnale="tolti:" + "|".join(sorted(tolti)), chiave="riapplica",
            azione="riapplica"))
    for registro in _registri_unreal(c.cartella, manifesto):
        t = _mtime(registro)
        if not t or t <= c.riferimento:
            continue
        testo = _leggi(registro)
        if not tolti and not re.search(r"Mounting pak file .*%s" % re.escape(adapter_unreal.PREFISSO_PAK), testo):
            problemi.append(Problema(
                "PAK_NON_CARICATO", "avviso", "Nell'ultima partita il gioco non ha caricato il file della traduzione: "
                "forse usa archivi firmati o un formato piu' nuovo. Crea un rapporto da inviare a chi ti aiuta con "
                "PatchSmith.", segnale="pak:%d" % int(t), azione="crea_rapporto"))
        origine = (c.profilo.get("lingua_origine") or "en").split("-")[0]
        lingue = set(re.findall(r"Overriding language with [^(]*\(([^)]+)\)", testo))
        altre = sorted(x for x in lingue if x.split("-")[0].lower() not in (origine, lingua.AUTO))
        if altre:
            problemi.append(Problema(
                "LINGUA_UNREAL", "info", "Nell'ultima partita il gioco usava la lingua '%s': la traduzione prende il "
                "posto di quella originale (%s). Scegli %s nelle opzioni del gioco." % (
                    altre[0], lingua.etichetta(origine), lingua.etichetta(origine)),
                segnale="lingua:%s:%d" % (altre[0], int(t))))
    try:
        attuali = {n: _sha1(pak.leggi(n)) for pak, n, _, _ in adapter_unreal.trova_locres(c.cartella)}
    except Exception:  # noqa: BLE001 - unreadable archives: "Analyze and translate" reports it
        attuali = None
    if attuali and manifesto.get("sorgenti") and attuali != manifesto.get("sorgenti"):
        problemi.append(Problema(
            "AGGIORNATO", "avviso", "Il gioco e' stato aggiornato: premi \"Analizza e traduci\" per tradurre anche i "
            "testi nuovi (quelli gia' tradotti restano).", segnale="sorgenti:" + _sha1(
                json.dumps(sorted(attuali.items())).encode("utf-8")), azione="traduci"))
    return problemi


def _registri_unreal(cartella, manifesto):
    """The Unreal game logs (%LOCALAPPDATA%\\<Progetto>\\Saved\\Logs\\<Progetto>.log) of the projects with a
    PatchSmith pak (<Progetto>/Content/Paks/<pak>), even if the chosen folder is the project's."""
    if not os.environ.get("LOCALAPPDATA"):
        return []
    progetti = set()
    for rel in manifesto.get("file") or {}:
        parti = os.path.normpath(os.path.join(cartella, *rel.split("/"))).split(os.sep)
        if len(parti) >= 4:
            progetti.add(parti[-4])
    return [os.path.join(os.environ["LOCALAPPDATA"], p, "Saved", "Logs", p + ".log") for p in sorted(progetti)]


def _sha1(dati):
    import hashlib
    return hashlib.sha1(dati).hexdigest()


def _verifica_xna(c):
    problemi = []
    manifesto = adapter_xna.leggi_manifesto(c.cartella)
    if not manifesto:
        return []
    tolti = [rel for rel in manifesto["file"] if not os.path.exists(os.path.join(c.cartella, *rel.split("/")))]
    if tolti:
        problemi.append(Problema(
            "TRADUZIONE_TOLTA", "avviso", "I file con la traduzione (%s) non ci sono piu' nel gioco: forse un "
            "aggiornamento. Li rimetto con le traduzioni salvate." % ", ".join(tolti[:2]),
            rimedio=lambda: _riapplica(c), sicuro=True, segnale="tolti:" + "|".join(sorted(tolti)), chiave="riapplica",
            azione="riapplica"))
    cambiati = []
    for nome, impronta in (manifesto.get("sorgenti") or {}).items():
        p = os.path.join(c.cartella, nome)
        try:
            if adapter_xna._sha256(p) != impronta:
                cambiati.append(nome)
        except OSError:
            cambiati.append(nome)
    if cambiati:
        problemi.append(Problema(
            "AGGIORNATO", "avviso", "Il gioco e' stato aggiornato (%s): premi \"Analizza e traduci\" per tradurre "
            "anche i testi nuovi." % ", ".join(cambiati[:2]), segnale="sorgenti:" + "|".join(sorted(cambiati)),
            azione="traduci"))
    return problemi


# --- crashes of the other engines (Windows Application log) -------------------------------------------------

def eventi_crash(dal):
    """Program crashes from the Windows Application log after the time 'dal' (at most GIORNI_EVENTI days ago), read
    only: [{id, istante, codice (1000 o 1026), app, percorso, testo}]. 1000: a program crash (with the executable's
    path); 1026: unhandled .NET error (only the executable's name, in the text). Empty if it cannot be read."""
    if not sys.platform.startswith("win"):
        return []
    secondi = max(60.0, min(time.time() - (dal or 0), GIORNI_EVENTI * 86400.0))
    domanda = "*[System[(EventID=1000 or EventID=1026) and TimeCreated[timediff(@SystemTime) <= %d]]]" % int(
        secondi * 1000)
    try:
        r = subprocess.run(["wevtutil", "qe", "Application", "/q:" + domanda, "/f:xml", "/rd:true", "/c:500"],
                           capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return leggi_eventi(r.stdout)


def leggi_eventi(dati):
    """The events from wevtutil's XML (bytes: UTF-8 or the Windows encoding)."""
    try:
        testo = dati.decode("utf-8")
    except UnicodeDecodeError:
        testo = dati.decode("mbcs" if sys.platform.startswith("win") else "latin-1", "replace")
    try:
        radice = ET.fromstring("<Eventi>%s</Eventi>" % testo)
    except ET.ParseError:
        return []
    ns = "{http://schemas.microsoft.com/win/2004/08/events/event}"
    eventi = []
    for ev in radice.iter(ns + "Event"):
        try:
            codice = int(ev.findtext("%sSystem/%sEventID" % (ns, ns)) or 0)
            tempo = ev.find("%sSystem/%sTimeCreated" % (ns, ns)).get("SystemTime")
            istante = datetime.datetime.strptime(tempo[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except (AttributeError, TypeError, ValueError):
            continue
        dati_ev = {d.get("Name") or str(i): d.text or "" for i, d in enumerate(ev.iter(ns + "Data"))}
        testo_ev = "\n".join(dati_ev.values())
        app, percorso = dati_ev.get("AppName"), dati_ev.get("AppPath")
        if codice == 1026:
            m = re.search(r"^[^:\n]*:\s*(\S+\.exe)\s*$", testo_ev, re.M | re.I)
            app = m.group(1) if m else None
        eventi.append({"id": ev.findtext("%sSystem/%sEventRecordID" % (ns, ns)) or "%d-%d" % (codice, istante),
                       "istante": istante, "codice": codice, "app": app, "percorso": percorso, "testo": testo_ev})
    return eventi


def _eseguibili(cartella):
    """The (lowercase) names of the game's executables: in the folder and in subfolders up to two levels deep."""
    nomi = set()
    for radice, dirs, files in os.walk(cartella):
        if os.path.relpath(radice, cartella).count(os.sep) >= 2:
            dirs[:] = []
        nomi |= {f.lower() for f in files if f.lower().endswith(".exe")}
    return nomi


def crash_del_gioco(cartella, eventi, dal):
    """The crash events of the game's executable (inside its folder) after 'dal'."""
    uscita, exe = [], None
    for ev in eventi:
        if ev["istante"] <= dal:
            continue
        if ev.get("percorso"):
            if _dentro(ev["percorso"], cartella):
                uscita.append(ev)
        elif ev.get("app"):
            if exe is None:
                exe = _eseguibili(cartella)
            if ev["app"].lower() in exe:
                uscita.append(ev)
    return uscita


def _verifica_crash_eventi(c, eventi):
    crash = crash_del_gioco(c.cartella, eventi if eventi is not None else eventi_crash(c.riferimento), c.riferimento)
    if not crash:
        return []
    recente = max(crash, key=lambda e: e["istante"])
    volte = "%d volte, l'ultima il %s" % (len(crash), _quando(recente["istante"])) if len(crash) > 1 else \
        "il %s" % _quando(recente["istante"])
    return [Problema("CRASH", "errore", "Il gioco si e' chiuso all'improvviso dopo la traduzione (%s). Se succede "
                     "ogni volta, premi \"Ripristina originali\" per tornare ai file originali (le traduzioni restano "
                     "salvate) e crea un rapporto." % volte, rimedio=lambda: _togli_file(c), sicuro=False,
                     segnale="eventi:" + ",".join(sorted(str(e["id"]) for e in crash)), azione="ripristina")]


def _togli_file(c):
    from . import auto as automatico
    d = {}
    automatico.ripristina(c.cartella, c.log, esito=d)
    e = d.get("esito")
    return (e is not None and e.codice == "OK"), (e.motivo if e is not None else "")


# --- installation and reference point ---------------------------------------------------------------------

def _installata(c):
    """True if the game has PatchSmith's translation for this game profile."""
    if not c.cartella or not os.path.isdir(c.cartella):
        return False
    if c.motore == "unity":
        return adapter_unity.manifesto_presente(c.cartella)
    if c.motore == "unreal":
        return adapter_unreal.manifesto_presente(c.cartella)
    if c.motore == "xna":
        return adapter_xna.manifesto_presente(c.cartella)
    if c.motore == "web-vivo":  # the script in preload.js (or its backup, if the game put the original back)
        from . import adapter_web_live as adapter_web_vivo
        preload = adapter_web_vivo.trova_preload(c.cartella)
        return bool(preload and leggi_info(preload))
    return any(_info_voce(c, v) for v in c.profilo.get("file") or [])


def _info_voce(c, voce):
    rel = voce.get("percorso") if isinstance(voce, dict) else None
    if not rel:
        return None
    info = leggi_info(os.path.join(c.cartella, *rel.split("/")))
    return info if info and not info.get("configuratore") else None


def riferimento(c):
    """The time of PatchSmith's last write to the game: manifest, command and plugin of the live translator,
    information of the translated files, fixes made by this check. Older signals do not count (they were already
    there when PatchSmith last wrote)."""
    tempi = [c.ultimo_rimedio()]
    if c.motore == "unity":
        for rel in (adapter_unity.MANIFESTO, adapter_unity.CONFIG_SERVIZIO, adapter_unity.PLUGIN_SERVIZIO):
            tempi.append(_mtime(os.path.join(c.cartella, *rel.split("/"))) or 0)
    elif c.motore == "unreal":
        tempi.append(_mtime(os.path.join(c.cartella, adapter_unreal.MANIFESTO)) or 0)
    elif c.motore == "xna":
        tempi.append(_mtime(os.path.join(c.cartella, adapter_xna.MANIFESTO)) or 0)
    for voce in c.profilo.get("file") or []:
        rel = voce.get("percorso") if isinstance(voce, dict) else None
        if rel:
            for suffisso in SUFFISSI_INFO:  # (also the name used by versions up to 0.12)
                tempi.append(_mtime(os.path.join(c.cartella, *rel.split("/")) + suffisso) or 0)
    return max(tempi)


def segnali(percorso_profilo, profilo=None):
    """{signal: value} of a game, quick to read (file dates): they change when playing, when the game updates and
    when PatchSmith writes. Empty if the translation is not installed. A completed check saves them in 'visti': when
    PatchSmith opens, only the games with different signals are checked (da_controllare)."""
    profilo = profilo if profilo is not None else _leggi_profilo(percorso_profilo)
    c = _Contesto(percorso_profilo, profilo, {}, None, False, None)
    if not _installata(c):
        return {}
    uscita = {}

    def data(chiave, percorso):
        """Date (to the millisecond) and size: a file put back right after a write still changes."""
        try:
            st = os.stat(percorso)
        except (OSError, TypeError, ValueError):
            return
        uscita[chiave] = "%d/%d" % (st.st_mtime_ns // 1_000_000, st.st_size)

    if c.motore == "unity":
        for rel in (adapter_unity.MANIFESTO, adapter_unity.LOG_BEPINEX, adapter_unity.STATO_SERVIZIO,
                    adapter_unity.CONFIG_SERVIZIO):
            data(rel, os.path.join(c.cartella, *rel.split("/")))
        manifesto = adapter_unity.leggi_manifesto(c.cartella) or {}
        uscita["versione"] = manifesto.get("patchsmith_versione") or "-"
        # PatchSmith updated: the games installed with the previous version are checked again (VERSIONE)
        uscita["patchsmith"] = __version__
        try:
            registri = registri_unity(c.cartella)
        except OSError:
            registri = {}
        for p in registri.get("player", []):
            data("unity:" + os.path.basename(p), p)
        for nome, istante, _ in crash_unity(registri):
            uscita["crash:" + nome] = int(istante)
        from .xunity_service import percorso_diario
        data("diario", percorso_diario(c.nome))
        if profilo.get("nome") and profilo.get("lingua_destinazione"):  # pending fixes (CORREZIONI_IN_ATTESA)
            from .corrections import percorso_da_scrivere
            data("da_scrivere", percorso_da_scrivere(profilo))
    elif c.motore in ("unreal", "xna"):
        modulo = adapter_unreal if c.motore == "unreal" else adapter_xna
        data("manifesto", os.path.join(c.cartella, modulo.MANIFESTO))
        manifesto = modulo.leggi_manifesto(c.cartella) or {}
        for rel in list(manifesto.get("file") or {}) + list(manifesto.get("sorgenti") or {}):
            data(rel, os.path.join(c.cartella, *rel.split("/")))
        if c.motore == "unreal":
            for registro in _registri_unreal(c.cartella, manifesto):
                data("log:" + os.path.splitext(os.path.basename(registro))[0], registro)
    else:
        for voce in profilo.get("file") or []:
            info = _info_voce(c, voce)
            if not info:
                continue
            p = os.path.join(c.cartella, *voce["percorso"].split("/"))
            data(voce["percorso"], p)
            for nome in info.get("creati", []):
                data("creato:" + nome, os.path.join(os.path.dirname(p), nome))
        if c.motore == "unity-localization":  # the Unity log: the letters the fonts lack (FONT_MANCANTI)
            try:
                for p in registri_unity(c.cartella).get("player", []):
                    data("unity:" + os.path.basename(p), p)
            except OSError:
                pass
        if c.motore == "gamemaker" and os.environ.get("LOCALAPPDATA"):
            for exe in sorted(_eseguibili(c.cartella))[:5]:
                opzioni = os.path.join(os.environ["LOCALAPPDATA"], os.path.splitext(exe)[0])
                for p in glob.glob(os.path.join(glob.escape(opzioni), "*"))[:20]:
                    if os.path.isfile(p):
                        data("opzioni:" + os.path.basename(p), p)
    return uscita


def da_controllare(eventi=None):
    """The game profiles of games with new signals since the last check (or never checked), with the translation
    installed: [game profile path]. Fast: only file dates (and one read of the Windows crash log for non-Unity
    games)."""
    from . import auto as automatico
    scelti, altri = [], []
    for percorso, profilo in automatico.elenco_profili():
        cartella = profilo.get("cartella_gioco")
        if not cartella or not os.path.isdir(cartella) or profilo.get("backend") == "finto":
            continue
        try:
            attuali = segnali(percorso, profilo)
        except Exception:  # noqa: BLE001 - an unreadable game does not stop the others
            continue
        if not attuali:
            continue
        if attuali != (leggi_stato(percorso).get("visti") or {}):
            scelti.append(percorso)
        elif profilo.get("motore") != "unity":
            altri.append((percorso, profilo))
    if altri:  # new Windows crashes for the games that have no other signals
        if eventi is None:
            eventi = eventi_crash(min(leggi_stato(p).get("eventi_dal") or 0 for p, _ in altri))
        for percorso, profilo in altri:
            stato = leggi_stato(percorso)
            gia = set(stato.get("eventi") or [])
            # only those after its last check: it has already seen the earlier ones (or they came before
            # PatchSmith's last write), otherwise it would be checked again at every startup
            nuovi = [e for e in crash_del_gioco(os.path.abspath(profilo["cartella_gioco"]), eventi,
                                                stato.get("eventi_dal") or 0) if str(e["id"]) not in gia]
            if nuovi:
                scelti.append(percorso)
    return scelti


# --- the check -------------------------------------------------------------------------------------------

def controlla(percorso_profilo, applica=True, log=print, traduttore=None, eventi=None, ritraduci=True):
    """Checks a game after playing. applica=True: the safe fixes start (with the game closed, once per signal) and
    the result is saved in profiles/<nome>.controllo.json; False: read only, writes nothing.
    traduttore: to re-translate the lines left provisional while playing (default: the game profile's one).
    ritraduci=False (the check when the interface opens): those lines are not re-translated now, they stay to do
    (FRASI_DA_RITRADURRE, action 'ritraduci'); the quick checks and their fixes are made anyway.
    eventi: the Windows crashes already read (for the tests; default: eventi_crash). Returns [Problema]."""
    log = log or (lambda _m: None)
    profilo = _leggi_profilo(percorso_profilo)
    stato = leggi_stato(percorso_profilo)
    c = _Contesto(percorso_profilo, profilo, stato, log, applica, traduttore, ritraduci, eventi)
    log("Controllo dopo aver giocato: %s" % c.titolo)
    if not _installata(c):
        log("  Traduzione non installata in %s: niente da controllare." % (c.cartella or "?"))
        if applica and stato:  # (e.g. removed with "Restore originals"): earlier problems no longer apply
            stato.update(visti={}, ultimo_controllo=_adesso(), ultimo={"quando": _adesso(), "corretti": 0,
                                                                      "da_fare": 0, "problemi": []})
            try:
                _salva_stato(percorso_profilo, stato)
            except OSError:
                pass
        return []
    c.riferimento = riferimento(c)
    log("  Conta solo quello che e' successo dopo l'ultima scrittura di PatchSmith nel gioco (%s)."
        % _quando(c.riferimento))
    if c.motore == "unity":
        problemi = _verifica_unity(c)
    elif c.motore == "web-vivo":
        problemi = _verifica_web_vivo(c)
    else:
        problemi = _verifica_file(c) if c.motore not in ("unreal", "xna") else []
        if c.motore == "unity-localization":  # (Unity's Player.log: without BepInEx it is the only log)
            problemi += _verifica_font(c, _registri_player(c))
        if c.motore == "unreal":
            problemi += _verifica_unreal(c)
        elif c.motore == "xna":
            problemi += _verifica_xna(c)
        crash = _verifica_crash_eventi(c, eventi)
        problemi += crash
    problemi += _verifica_temporanei(c)
    for p in problemi:  # the technical details only in the log
        if p.dettaglio:
            log("  (%s: %s)" % (p.codice, p.dettaglio))
    _esegui_rimedi(c, problemi)
    if any(p.stato == "rimandato" for p in problemi):
        # game open: nothing is written, not even the result (the next check starts over)
        applica = False
    if applica:
        stato["visti"] = segnali(percorso_profilo, profilo)
        if c.motore != "unity":  # the Windows crashes already seen (they are not files: they are not in 'visti')
            visti = {i for p in problemi if p.segnale.startswith("eventi:") for i in p.segnale[7:].split(",")}
            stato["eventi"] = sorted(set(stato.get("eventi") or []) | visti)[-200:]
            stato["eventi_dal"] = time.time()
        # the journal lines already seen (redone, or tried once) are not picked up again: "Analyze and translate"
        # retries the remaining ones (provisional cache lines, see automatico._righe_cache_da_rifare)
        for p in problemi:
            if p.codice == "FRASI_DA_RIFARE" and p.stato in ("corretto", "non_riuscito", "gia_tentato"):
                c.diario_fino = max(c.diario_fino or "", p.extra.get("diario_fino") or "")
        if c.diario_fino:
            stato["diario_fino"] = c.diario_fino
    if applica:
        stato["ultimo_controllo"] = _adesso()
        stato["versione"] = __version__
        stato["ultimo"] = {"quando": _adesso(), "corretti": sum(p.stato == "corretto" for p in problemi),
                           "da_fare": sum(p.stato in DA_FARE for p in problemi),
                           "problemi": [p.come_dizionario() for p in problemi]}
        try:
            _salva_stato(percorso_profilo, stato)
        except OSError as e:
            log("  ! Non ho potuto salvare il risultato del controllo (%s)" % e)
    for riga in righe(problemi):
        log("  " + riga)
    return problemi


DA_FARE = ("da_fare", "non_riuscito", "gia_tentato")


def _verifica_temporanei(c):
    """Temporary files left in the translation memory by an interrupted save (e.g. the game closed suddenly while
    the live translator was saving): those of this game's translation memory and of the shared memory."""
    cartella = os.path.dirname(percorso_memoria(c.profilo))
    adesso = time.time()
    vecchi = [p for p in glob.glob(os.path.join(glob.escape(cartella), "*.tmp"))
              if os.path.basename(p).startswith((c.nome + ".", "comune.")) and os.path.isfile(p)
              and adesso - (_mtime(p) or adesso) > file_condiviso.VECCHIO_TEMPORANEO]
    if not vecchi:
        return []
    def togli():
        tolti = 0
        for p in vecchi:
            try:
                if time.time() - os.path.getmtime(p) > file_condiviso.VECCHIO_TEMPORANEO:
                    os.remove(p)
                    tolti += 1
            except OSError:
                pass
        return "tolti %d" % tolti

    return [Problema("TEMPORANEI", "info", "Nella memoria delle traduzioni %s di un salvataggio interrotto: %s tolgo "
                     "(le traduzioni sono al sicuro nei file veri)." % (
                         "è rimasto un file temporaneo" if len(vecchi) == 1 else
                         "sono rimasti %d file temporanei" % len(vecchi), "lo" if len(vecchi) == 1 else "li"),
                     rimedio=togli, sicuro=True, segnale="tmp:" + "|".join(sorted(os.path.basename(p)
                                                                                     for p in vecchi)))]


def _gia_tentato(stato, chiave, segnale):
    return next((v for v in (stato.get("rimedi") or {}).get(chiave) or [] if isinstance(v, dict)
                 and v.get("segnale") == segnale), None)


def _annota(stato, chiave, segnale, riuscito, dettaglio):
    voci = [v for v in (stato.setdefault("rimedi", {}).get(chiave) or []) if isinstance(v, dict)]
    voci.append({"segnale": segnale, "quando": _adesso(), "ts": time.time(), "riuscito": bool(riuscito),
                 "dettaglio": str(dettaglio)[:300]})
    stato["rimedi"][chiave] = voci[-MAX_TENTATIVI_SALVATI:]


def _esegui_rimedi(c, problemi):
    """The safe fixes, with the game closed, once per signal (applica=True); the others stay to do."""
    candidati = [p for p in problemi if p.rimedio is not None and p.sicuro]
    if c.applica and candidati and c.aperto():
        c.log("  Il gioco e' aperto: le correzioni le faccio al prossimo controllo, a gioco chiuso.")
    fatti, azioni = {}, {}
    for p in problemi:
        if p.rimedio is None or not p.sicuro:
            p.stato = "da_fare" if p.azione else "info"
            continue
        chiave = p.chiave or p.codice
        tentato = _gia_tentato(c.stato, chiave, p.segnale)
        if tentato:
            p.stato = "gia_tentato"
            p.dettaglio = "gia' provato a correggerlo il %s (%s), ma il problema c'e' ancora" % (
                _quando(tentato.get("ts")), tentato.get("dettaglio") or "")
            continue
        if not c.applica:
            p.stato = "da_correggere"
            continue
        if c.aperto():
            p.stato = "rimandato"
            continue
        if chiave not in fatti:
            interruzione.controlla()
            try:
                risultato = p.rimedio()
                if not isinstance(risultato, tuple):
                    risultato = (True, risultato or "")
                riuscito, testo = risultato[:2]
                if len(risultato) > 2:  # the right button if it is still to do (e.g. install the local translator)
                    azioni[chiave] = risultato[2]
            except interruzione.Interrotto:
                raise
            except Exception as e:  # noqa: BLE001 - a failed fix does not stop the others
                riuscito, testo = False, str(e) if isinstance(e, (ErroreControllo, adapter_unity.ErroreUnity,
                                                                  ErroreBackend)) else \
                    "%s: %s" % (type(e).__name__, e)
            fatti[chiave] = (riuscito, testo)
            c.log("  %s %s" % ("-> corretto:" if riuscito else "-> non riuscito:", testo))
        riuscito, testo = fatti[chiave]
        _annota(c.stato, chiave, p.segnale, riuscito, testo)
        p.stato = "corretto" if riuscito else "non_riuscito"
        p.dettaglio = testo
        p.azione = azioni.get(chiave, p.azione)


# --- results for the interface and the command line ----------------------------------------------------------

ETICHETTE = {"corretto": "corretto", "non_riuscito": "da fare (correzione non riuscita)",
             "gia_tentato": "da fare (gia' corretto una volta, ma e' tornato)", "rimandato": "da correggere a gioco "
             "chiuso", "da_fare": "da fare", "da_correggere": "si correggerebbe da solo", "info": "da sapere"}


def righe(problemi):
    """The summary for the log and the command line."""
    if not problemi:
        return ["Nessun problema trovato."]
    uscita = []
    for p in problemi:
        uscita.append("[%s] %s" % (ETICHETTE.get(p.stato, p.stato), p.messaggio))
        if p.dettaglio and p.stato != "info":
            uscita.append("    %s" % p.dettaglio)
    corretti = sum(p.stato == "corretto" for p in problemi)
    da_fare = sum(p.stato in DA_FARE for p in problemi)
    da_correggere = sum(p.stato in ("da_correggere", "rimandato") for p in problemi)
    uscita.append("Problemi corretti: %d, da fare: %d%s." % (corretti, da_fare, ", da correggere a gioco chiuso o "
                                                             "senza sola lettura: %d" % da_correggere
                                                             if da_correggere else ""))
    return uscita


def come_esito(problemi, percorso_profilo=None, titolo=None):
    """The outcome (esito.Esito) of a game's check, for the interface window: OK (nothing to do), PARZIALE
    (something to do), GIOCO_APERTO (fixes postponed). In extra: 'controllo' (the problems as dictionaries),
    'gioco', 'cartella', 'profilo', 'corretti', 'da_fare'."""
    profilo = {}
    if percorso_profilo:
        try:
            profilo = _leggi_profilo(percorso_profilo)
        except (OSError, ValueError):
            profilo = {}
    gioco = titolo or os.path.basename(os.path.normpath(profilo.get("cartella_gioco") or "")) or "il gioco"
    corretti = [p for p in problemi if p.stato == "corretto"]
    da_fare = [p for p in problemi if p.stato in DA_FARE]
    rimandati = [p for p in problemi if p.stato == "rimandato"]
    azioni = []
    for p in da_fare:
        if p.azione:
            azioni.append(p.azione)
    if any(p.stato in ("non_riuscito", "gia_tentato") for p in da_fare):
        azioni.append("crea_rapporto")
    if rimandati:
        codice, azioni = "GIOCO_APERTO", ["chiudi_gioco"] + azioni
        motivo = "Il gioco e' aperto: le correzioni le faccio quando e' chiuso."
    elif da_fare:
        codice = "PARZIALE"
        motivo = "Dopo l'ultima partita a %s: %s, %d da fare." % (gioco, _quanti(len(corretti)), len(da_fare))
    else:
        codice = "OK"
        motivo = ("Dopo l'ultima partita a %s: %s." % (gioco, _quanti(len(corretti))) if corretti else
                  "Dopo l'ultima partita a %s: nessun problema." % gioco)
    return Esito(codice, motivo, motore=profilo.get("motore"), azioni=azioni,
                 extra={"controllo": [p.come_dizionario() for p in problemi], "gioco": gioco,
                        "cartella": profilo.get("cartella_gioco"), "profilo": percorso_profilo,
                        "corretti": len(corretti), "da_fare": len(da_fare)})


def _quanti(n):
    return "1 problema corretto" if n == 1 else "%d problemi corretti" % n


def problema_errore(errore):
    """The check of a game failed (unexpected error): a problem to do, with the report."""
    return Problema("ERRORE", "errore", "Il controllo non e' riuscito (%s: %s): puoi creare un rapporto da inviare a "
                    "chi ti aiuta con PatchSmith." % (type(errore).__name__, str(errore)[:200]), azione="crea_rapporto",
                    stato="da_fare")


def ultimo(percorso_profilo):
    """The result of the last saved check: {quando, corretti, da_fare, problemi} or {}."""
    dati = leggi_stato(percorso_profilo).get("ultimo")
    return dati if isinstance(dati, dict) else {}


def esito_salvato(percorso_profilo):
    """The outcome of the last saved check (like come_esito), or None if there is none."""
    dati = ultimo(percorso_profilo)
    if not dati:
        return None
    problemi = []
    for d in dati.get("problemi") or []:
        if isinstance(d, dict) and d.get("messaggio"):
            problemi.append(Problema(d.get("codice", "?"), d.get("gravita", "info"), d["messaggio"],
                                     sicuro=bool(d.get("sicuro")), segnale=d.get("segnale") or "",
                                     azione=d.get("azione") if d.get("azione") in _azioni() else None,
                                     stato=d.get("stato") if d.get("stato") in STATI else "info",
                                     dettaglio=d.get("dettaglio") or "",
                                     extra={"frasi": d["frasi"]} if isinstance(d.get("frasi"), int) else {}))
    return come_esito(problemi, percorso_profilo)


def _azioni():
    from .outcome import AZIONI
    return AZIONI
