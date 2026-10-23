"""Electron games whose game files are unreadable (e.g. encrypted with GemShell) but with resources/app/preload.js
in clear text: the texts are translated while the game runs, in its page, as XUnity does with Unity.

PatchSmith neither opens nor changes the encrypted file: it appends to preload.js (with a verified backup, which
"Restore originals" puts back) the injector_live.js script, which
- translates the page's texts with patchsmith-dizionario.json (the translations already made, from the game's
  translation memory);
- asks PatchSmith's translator for new sentences (servizio_xunity: the same service as for Unity games, on
  127.0.0.1, started by preload.js with the command in patchsmith-vivo.json and closed with the game), which saves
  them to the translation memory.

It needs a preload.js that runs in the page with Node: in Electron's main.js contextIsolation:false and
sandbox:false (as in games made with GemShell). Otherwise the game cannot be translated this way.
"""
import hashlib
import json
import os
import re
import sys

from .adapter_webjson import scrivi_atomico

# marker of our script inside the game's preload.js (first comment of injector_live.js): never change it, or the
# games translated before would no longer be recognized
SEGNO = "Patchsmith - traduzione dal vivo"
NOME_DIZIONARIO = "patchsmith-dizionario.json"
NOME_CONFIG = "patchsmith-vivo.json"
_ISOLATO = re.compile(r"contextIsolation\s*:\s*false")
_SANDBOX = re.compile(r"sandbox\s*:\s*false")


def trova_preload(cartella, profondita=4):
    """The game's resources/app/preload.js (also in a subfolder, e.g. game/), or None."""
    cartella = os.path.abspath(cartella)
    for radice, dirs, files in os.walk(cartella):
        if os.path.relpath(radice, cartella).count(os.sep) >= profondita:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules", "locales", "steam_settings")]
        if os.path.basename(radice).lower() == "app" and os.path.basename(os.path.dirname(radice)).lower() == "resources" \
                and "preload.js" in files and "main.js" in files:
            return os.path.join(radice, "preload.js")
    return None


def adatto(cartella):
    """preload.js if the game can be translated live (the preload runs in the page and has Node), otherwise None."""
    preload = trova_preload(cartella)
    if not preload:
        return None
    try:
        with open(os.path.join(os.path.dirname(preload), "main.js"), encoding="utf-8", errors="replace") as f:
            principale = f.read()
    except OSError:
        return None
    return preload if _ISOLATO.search(principale) and _SANDBOX.search(principale) else None


def installato(cartella):
    preload = trova_preload(cartella)
    if not preload:
        return False
    with open(preload, "rb") as f:
        return SEGNO.encode("utf-8") in f.read()


def _comando(percorso_profilo, nome_profilo):
    from . import adapter_unity, xunity_service as servizio_xunity
    from .project import CARTELLA_PROGETTO
    porta = servizio_xunity.porta_per(nome_profilo)
    return porta, {"programma": adapter_unity._python_senza_finestra(), "cartella": CARTELLA_PROGETTO,
                   "argomenti": ["-I", os.path.join(CARTELLA_PROGETTO, "smith.py"), "servizio-xunity", "--profilo",
                                 os.path.abspath(percorso_profilo), "--porta", str(porta)]}


def installa(cartella, percorso_profilo, nome_profilo, da, a, traduzioni, log=print, attivo=True):
    """Adds (or updates) the script in preload.js and writes the dictionary and the translator's command.
    attivo=False: no translator while playing (only the dictionary's translations). Returns the number of
    translations."""
    from . import project as progetto
    preload = adatto(cartella)
    if not preload:
        raise ValueError("questo gioco non permette la traduzione dal vivo (preload.js isolato o assente)")
    app = os.path.dirname(preload)
    with open(preload, "rb") as f:
        attuale = f.read()
    info = progetto.leggi_info(preload)
    backup = preload + progetto.SUFFISSO_BACKUP
    if SEGNO.encode("utf-8") in attuale and info and os.path.exists(backup):
        with open(backup, "rb") as f:  # already ours: start again from the saved original
            originale = f.read()
    else:  # first time, or the game put back its own preload.js (an update): the backup is of this one
        progetto.crea_backup(preload, extra={"creati": [NOME_DIZIONARIO, NOME_CONFIG], "web_vivo": True})
        originale = attuale
    porta, comando = _comando(percorso_profilo, nome_profilo)
    config = {"dizionario": NOME_DIZIONARIO, "porta": porta if attivo else 0, "da": da, "a": a,
              "comando": comando if attivo else None}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "injector_live.js"), encoding="utf-8") as f:
        script = f.read().replace("/*__CONFIG__*/{}", json.dumps(config, ensure_ascii=True))
    dizionario = {k: v for k, v in traduzioni.items() if isinstance(k, str) and isinstance(v, str) and v and v != k}
    scrivi_atomico(os.path.join(app, NOME_DIZIONARIO),
                   json.dumps(dizionario, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8"))
    scrivi_atomico(os.path.join(app, NOME_CONFIG), json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"))
    nuovo = originale.rstrip(b"\r\n") + b"\n" + script.encode("utf-8")
    if nuovo != attuale:
        scrivi_atomico(preload, nuovo)
    info = progetto.leggi_info(preload) or {}
    impronta = hashlib.sha256(nuovo).hexdigest()
    if impronta not in info.get("hash_scritti", []):
        info["hash_scritti"] = (info.get("hash_scritti") or [])[-(progetto.MAX_IMPRONTE - 1):] + [impronta]
        progetto.salva_info(preload, info)
    log("  Traduzione dal vivo installata in %s (con backup): %d traduzioni gia' pronte%s" % (
        os.path.relpath(preload, cartella), len(dizionario), ", le altre arrivano mentre giochi" if attivo else
        " (traduttore mentre giochi spento)"))
    if not os.path.exists(comando["programma"]) and sys.platform.startswith("win"):
        log("  ! Non trovo Python (%s): le frasi nuove resteranno in originale" % comando["programma"])
    return len(dizionario)
