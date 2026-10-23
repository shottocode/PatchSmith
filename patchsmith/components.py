"""Componenti esterni (loader e plugin) scaricati dalle release ufficiali, a versioni fissate.

Ogni componente ha URL, dimensione e impronta SHA-256 scritti qui: il file scaricato viene usato
solo se l'impronta corrisponde. I file restano in components/ (cache), cosi' si scaricano una volta.
"""
import hashlib
import os
import urllib.request
import zipfile

from . import interruption as interruzione
from .project import cartella_componenti

CARTELLA = None  # None: components/ accanto al programma (progetto.cartella_componenti); le prove la cambiano


def cartella():
    return CARTELLA or cartella_componenti()

# nome -> {url, file, dimensione, sha256, licenza, versione}
# Impronte lette dall'API delle release GitHub ufficiali (campo "digest") il 2026-10-13.
CATALOGO = {
    "bepinex5_x64": {
        "url": "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x64_5.4.23.5.zip",
        "file": "BepInEx_win_x64_5.4.23.5.zip", "dimensione": 639118, "versione": "5.4.23.5",
        "sha256": "82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4", "licenza": "LGPL-2.1",
    },
    "bepinex5_x86": {
        "url": "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x86_5.4.23.5.zip",
        "file": "BepInEx_win_x86_5.4.23.5.zip", "dimensione": 638544, "versione": "5.4.23.5",
        "sha256": "37651c79e40d6f909572a4f461ac25350bb3ef8fe7fbd29f1aa8791a33b84c82", "licenza": "LGPL-2.1",
    },
    "xunity_bepinex5": {
        "url": "https://github.com/bbepis/XUnity.AutoTranslator/releases/download/v5.6.3.1/"
               "XUnity.AutoTranslator-BepInEx-5.6.3.1.zip",
        "file": "XUnity.AutoTranslator-BepInEx-5.6.3.1.zip", "dimensione": 759511, "versione": "5.6.3.1",
        "sha256": "6c3ff03aafe9708f25c2115c0c04c255b6a9d88e716896ae7bacb590a8777609", "licenza": "MIT",
    },
}


class ErroreComponente(Exception):
    pass


def _sha256(percorso):
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def percorso(nome):
    return os.path.join(cartella(), CATALOGO[nome]["file"])


def disponibile(nome):
    """True se il componente e' gia' in cache e integro."""
    p = percorso(nome)
    return os.path.exists(p) and _sha256(p) == CATALOGO[nome]["sha256"]


def ottieni(nome, log=print):
    """Percorso del file del componente, scaricandolo se serve. Verifica sempre l'impronta."""
    voce = CATALOGO[nome]
    destinazione = percorso(nome)
    if disponibile(nome):
        return destinazione
    os.makedirs(cartella(), exist_ok=True)
    temporaneo = destinazione + ".scaricamento"
    log("  scarico %s (%d KB) da %s" % (voce["file"], voce["dimensione"] // 1024, voce["url"]))
    req = urllib.request.Request(voce["url"], headers={"User-Agent": "Mozilla/5.0 (patchsmith)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as risposta, open(temporaneo, "wb") as f:
            letti = 0
            for blocco in iter(lambda: risposta.read(1 << 16), b""):
                interruzione.controlla()
                letti += len(blocco)
                if letti > voce["dimensione"] * 2 + 1_000_000:
                    raise ErroreComponente("%s: il file scaricato e' troppo grande, scaricamento interrotto" % nome)
                f.write(blocco)
    except BaseException as e:  # anche "Ferma": niente scaricamenti a meta'
        if os.path.exists(temporaneo):
            os.remove(temporaneo)
        if isinstance(e, OSError):
            raise ErroreComponente("Scaricamento di %s non riuscito: %s" % (nome, e)) from e
        raise
    impronta = _sha256(temporaneo)
    if impronta != voce["sha256"]:
        os.remove(temporaneo)
        raise ErroreComponente("%s: impronta SHA-256 diversa da quella attesa (%s): file scartato per sicurezza"
                               % (nome, impronta))
    os.replace(temporaneo, destinazione)
    return destinazione


def elenco_zip(nome):
    """Percorsi (con /) contenuti nello zip del componente."""
    with zipfile.ZipFile(percorso(nome)) as z:
        return [i.filename for i in z.infolist()]
