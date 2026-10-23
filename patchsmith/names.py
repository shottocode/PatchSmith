"""Names of the game not to translate: the studio and the title ("Cup Dog Games" does not become
"Giochi di cani da coppa").

They are read without changing anything:
- from Unity: app.info in the _Data folder (first line the company, second the product);
- from the version information of the game's executables (CompanyName and ProductName, as Windows shows them
  in Properties > Details): they are read as data, the program is not started;
- from Steam: the game's name in its manifest, if the game is installed from Steam.

They end up in the game profile (profilo["non_tradurre"]) and Progetto protects them like the game's codes
(testo.proteggi).
"""
import os
import re
import sys

# company forms at the end of the name: the game usually writes the name without them ("Cup Dog Games", not "... LLC")
_FORMA_SOCIETARIA = re.compile(r"[\s,]+(?:llc|l\.l\.c\.|inc\.?|ltd\.?|limited|gmbh|s\.?r\.?l\.?|s\.?p\.?a\.?|"
                               r"co\.?|corp\.?|corporation|ab|oy|kk|k\.k\.|pty\.? ltd\.?|bv|b\.v\.|sas|s\.a\.)$", re.I)
# names that say nothing about the game (default values of Unity or of the tools)
_GENERICI = {"defaultcompany", "unity", "unity technologies", "unity technologies aps", "microsoft corporation",
             "game", "gioco", "my project", "new unity project", "unknown", "none",
             "bootstrappackagedgame", "epic games", "epic games, inc.", "unreal engine", "nw.js", "nwjs", "electron",
             "github, inc.", "the nw.js community", "yoyo games ltd", "gamemaker", "mono", "monogame",
             # utility programs next to the game (Epic Online Services, Xbox/Microsoft Store, Blizzard)
             "epic online services bootstrapper", "microsoft game launch helper", "blizzarderror"}
_GENERICI = {g.rstrip(".") for g in _GENERICI}  # compared without the final period (as _buono cleans them)
MAX_ESEGUIBILI = 3


def _buono(nome):
    """The cleaned name, or None if it is of no use (empty, generic, too short or long, only digits)."""
    if not isinstance(nome, str):
        return None
    nome = " ".join(nome.replace("\x00", " ").split()).strip(" .,;:-©®™")
    if not 2 <= len(nome) <= 60 or not re.search(r"[^\W\d_]", nome) or nome.lower() in _GENERICI:
        return None
    if " " not in nome and len(nome) < 4:  # short acronyms ("GM", "UE"): too easy to find inside the texts
        return None
    return nome


def _varianti(nome):
    """The name and, if any, the same without the company form ('Cup Dog Games LLC' -> also 'Cup Dog Games')."""
    nome = _buono(nome)
    if not nome:
        return []
    uscita = [nome]
    corto = nome
    for _ in range(3):  # several forms in a row too: 'SQUARE ENIX CO., LTD.' -> 'SQUARE ENIX'
        tolto = _FORMA_SOCIETARIA.sub("", corto).rstrip(" .,")
        if tolto == corto:
            break
        corto = tolto
    corto = _buono(corto)
    if corto and corto != nome:
        uscita.append(corto)
    return uscita


def versione_exe(percorso):
    """{'CompanyName': ..., 'ProductName': ...} from the version information of a Windows executable (read
    as data with the Windows functions: the program is not started). Empty dictionary if there is none."""
    if not sys.platform.startswith("win"):
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        versione = ctypes.WinDLL("version", use_last_error=True)
        versione.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        versione.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        versione.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        versione.GetFileVersionInfoW.restype = wintypes.BOOL
        versione.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                            ctypes.POINTER(wintypes.UINT)]
        versione.VerQueryValueW.restype = wintypes.BOOL
        dimensione = versione.GetFileVersionInfoSizeW(percorso, None)
        if not dimensione:
            return {}
        dati = ctypes.create_string_buffer(dimensione)
        if not versione.GetFileVersionInfoW(percorso, 0, dimensione, dati):
            return {}
        puntatore, lunghezza = ctypes.c_void_p(), wintypes.UINT()
        codici = []
        if versione.VerQueryValueW(dati, "\\VarFileInfo\\Translation", ctypes.byref(puntatore),
                                   ctypes.byref(lunghezza)) and puntatore.value and lunghezza.value >= 4:
            parole = (wintypes.WORD * (lunghezza.value // 2)).from_address(puntatore.value)
            codici = ["%04x%04x" % (parole[i], parole[i + 1]) for i in range(0, len(parole) - 1, 2)]
        uscita = {}
        for codice in list(dict.fromkeys(codici + ["040904b0", "040904e4", "000004b0"])):
            for chiave in ("CompanyName", "ProductName"):
                if chiave in uscita:
                    continue
                if versione.VerQueryValueW(dati, "\\StringFileInfo\\%s\\%s" % (codice, chiave),
                                           ctypes.byref(puntatore), ctypes.byref(lunghezza)) \
                        and puntatore.value and lunghezza.value:
                    valore = ctypes.wstring_at(puntatore.value, lunghezza.value).split("\x00")[0].strip()
                    if valore:
                        uscita[chiave] = valore
        return uscita
    except (OSError, AttributeError, ValueError):
        return {}


def _eseguibili(cartella):
    """The game's executables in its folder (not installers, crash reporters and the like), largest first."""
    from .steam import _ESEGUIBILI_GENERICI
    try:
        nomi = [n for n in os.listdir(cartella) if n.lower().endswith(".exe") and not _ESEGUIBILI_GENERICI.match(n)]
    except OSError:
        return []
    percorsi = [os.path.join(cartella, n) for n in nomi if os.path.isfile(os.path.join(cartella, n))]

    def dimensione(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    return sorted(percorsi, key=dimensione, reverse=True)[:MAX_ESEGUIBILI]


def nomi_del_gioco(cartella, log=None):
    """The names not to translate for the game in the folder (see above), without duplicates. Raises no errors: a
    name that cannot be read is skipped."""
    trovati = []
    try:
        from . import language_unity as lingua_unity
        dati = lingua_unity.cartella_dati(cartella)
        if dati:
            trovati += [n for n in lingua_unity.azienda_prodotto(dati) if n]
    except (OSError, ValueError):
        pass
    for exe in _eseguibili(cartella):
        info = versione_exe(exe)
        trovati += [info.get("CompanyName"), info.get("ProductName")]
    try:
        from . import steam
        installato = steam.gioco_installato(cartella)
        if installato:
            trovati.append(installato.get("nome"))
    except Exception:  # noqa: BLE001 - Steam missing or unreadable: do without it
        pass
    uscita = []
    for nome in trovati:
        for variante in _varianti(nome):
            if variante not in uscita:
                uscita.append(variante)
    if log and uscita:
        log("  Nomi del gioco che restano come sono nella traduzione: %s" % ", ".join(uscita))
    return uscita


def parole_comuni(testi):
    """The words written in lowercase in the texts: a one-word name that also appears that way ('Control', 'mine')
    is an ordinary word of the game, and must be translated."""
    parole = set()
    for t in testi:
        if isinstance(t, str):
            parole.update(re.findall(r"\b[^\W\d_A-Z]+\b", t))
    return parole


def da_proteggere(nomi, comuni):
    """The game profile's names that really need protecting: multi-word ones always, one-word ones only if they do
    not also appear in lowercase in the game's texts (see parole_comuni)."""
    uscita = []
    for nome in nomi or ():
        nome = _buono(nome)
        if not nome:
            continue
        if " " not in nome and nome.lower() in comuni and nome.lower() != nome:
            continue
        if nome not in uscita:
            uscita.append(nome)
    return uscita
