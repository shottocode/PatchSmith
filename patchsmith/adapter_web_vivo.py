"""Giochi Electron con i file del gioco illeggibili (es. cifrati con GemShell) ma con resources/app/preload.js in
chiaro: i testi si traducono mentre il gioco gira, nella sua pagina, come fa XUnity con Unity.

PatchSmith non apre ne' cambia il file cifrato: aggiunge in fondo a preload.js (con backup verificato, che
"Ripristina originali" rimette) lo script iniettore_vivo.js, che
- traduce i testi della pagina con patchsmith-dizionario.json (le traduzioni gia' fatte, dalla memoria del gioco);
- chiede le frasi nuove al traduttore di PatchSmith (servizio_xunity: stesso servizio dei giochi Unity, su 127.0.0.1,
  avviato da preload.js con il comando in patchsmith-vivo.json e chiuso con il gioco), che le salva in memoria.

Serve un preload.js che giri nella pagina con Node: nel main.js di Electron contextIsolation:false e sandbox:false
(come nei giochi fatti con GemShell). Altrimenti il gioco non si puo' tradurre cosi'.
"""
import hashlib
import json
import os
import re
import sys

from .adapter_webjson import scrivi_atomico

SEGNO = "Patchsmith - traduzione dal vivo"
NOME_DIZIONARIO = "patchsmith-dizionario.json"
NOME_CONFIG = "patchsmith-vivo.json"
_ISOLATO = re.compile(r"contextIsolation\s*:\s*false")
_SANDBOX = re.compile(r"sandbox\s*:\s*false")


def trova_preload(cartella, profondita=4):
    """resources/app/preload.js del gioco (anche in una sottocartella, es. game/), o None."""
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
    """preload.js se il gioco si puo' tradurre dal vivo (il preload gira nella pagina e ha Node), se no None."""
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
    from . import adapter_unity, servizio_xunity
    from .progetto import CARTELLA_PROGETTO
    porta = servizio_xunity.porta_per(nome_profilo)
    return porta, {"programma": adapter_unity._python_senza_finestra(), "cartella": CARTELLA_PROGETTO,
                   "argomenti": ["-I", os.path.join(CARTELLA_PROGETTO, "smith.py"), "servizio-xunity", "--profilo",
                                 os.path.abspath(percorso_profilo), "--porta", str(porta)]}


def installa(cartella, percorso_profilo, nome_profilo, da, a, traduzioni, log=print, attivo=True):
    """Aggiunge (o aggiorna) lo script in preload.js e scrive dizionario e comando del traduttore. attivo=False: senza
    traduttore mentre si gioca (solo le traduzioni del dizionario). Restituisce il numero di traduzioni."""
    from . import progetto
    preload = adatto(cartella)
    if not preload:
        raise ValueError("questo gioco non permette la traduzione dal vivo (preload.js isolato o assente)")
    app = os.path.dirname(preload)
    with open(preload, "rb") as f:
        attuale = f.read()
    info = progetto.leggi_info(preload)
    backup = preload + progetto.SUFFISSO_BACKUP
    if SEGNO.encode("utf-8") in attuale and info and os.path.exists(backup):
        with open(backup, "rb") as f:  # gia' nostro: si riparte dall'originale salvato
            originale = f.read()
    else:  # prima volta, o il gioco ha rimesso il suo preload.js (aggiornamento): il backup e' di questo
        progetto.crea_backup(preload, extra={"creati": [NOME_DIZIONARIO, NOME_CONFIG], "web_vivo": True})
        originale = attuale
    porta, comando = _comando(percorso_profilo, nome_profilo)
    config = {"dizionario": NOME_DIZIONARIO, "porta": porta if attivo else 0, "da": da, "a": a,
              "comando": comando if attivo else None}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "iniettore_vivo.js"), encoding="utf-8") as f:
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
