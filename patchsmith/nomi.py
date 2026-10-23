"""Nomi del gioco da non tradurre: lo studio e il titolo ("Cup Dog Games" non diventa "Giochi di cani da coppa").

Si leggono, senza modificare nulla:
- da Unity: app.info nella cartella _Data (prima riga l'azienda, seconda il prodotto);
- dalle informazioni sulla versione degli eseguibili del gioco (CompanyName e ProductName, come le mostra Windows
  in Proprieta' > Dettagli): si leggono come dati, il programma non parte;
- da Steam: il nome del gioco nel suo manifesto, se il gioco e' installato da Steam.

Finiscono nel profilo (profilo["non_tradurre"]) e Progetto li protegge come i codici del gioco (testo.proteggi).
"""
import os
import re
import sys

# forme societarie in fondo al nome: il gioco scrive di solito il nome senza ("Cup Dog Games", non "... LLC")
_FORMA_SOCIETARIA = re.compile(r"[\s,]+(?:llc|l\.l\.c\.|inc\.?|ltd\.?|limited|gmbh|s\.?r\.?l\.?|s\.?p\.?a\.?|"
                               r"co\.?|corp\.?|corporation|ab|oy|kk|k\.k\.|pty\.? ltd\.?|bv|b\.v\.|sas|s\.a\.)$", re.I)
# nomi che non dicono nulla del gioco (valori predefiniti di Unity o degli strumenti)
_GENERICI = {"defaultcompany", "unity", "unity technologies", "unity technologies aps", "microsoft corporation",
             "game", "gioco", "my project", "new unity project", "unknown", "none",
             "bootstrappackagedgame", "epic games", "epic games, inc.", "unreal engine", "nw.js", "nwjs", "electron",
             "github, inc.", "the nw.js community", "yoyo games ltd", "gamemaker", "mono", "monogame",
             # programmi di servizio accanto al gioco (Epic Online Services, Xbox/Microsoft Store, Blizzard)
             "epic online services bootstrapper", "microsoft game launch helper", "blizzarderror"}
_GENERICI = {g.rstrip(".") for g in _GENERICI}  # si confrontano senza il punto finale (come li pulisce _buono)
MAX_ESEGUIBILI = 3


def _buono(nome):
    """Il nome ripulito, oppure None se non serve (vuoto, generico, troppo corto o lungo, solo numeri)."""
    if not isinstance(nome, str):
        return None
    nome = " ".join(nome.replace("\x00", " ").split()).strip(" .,;:-©®™")
    if not 2 <= len(nome) <= 60 or not re.search(r"[^\W\d_]", nome) or nome.lower() in _GENERICI:
        return None
    if " " not in nome and len(nome) < 4:  # sigle corte ("GM", "UE"): troppo facile trovarle dentro i testi
        return None
    return nome


def _varianti(nome):
    """Il nome e, se c'e', lo stesso senza la forma societaria ('Cup Dog Games LLC' -> anche 'Cup Dog Games')."""
    nome = _buono(nome)
    if not nome:
        return []
    uscita = [nome]
    corto = nome
    for _ in range(3):  # anche piu' forme di seguito: 'SQUARE ENIX CO., LTD.' -> 'SQUARE ENIX'
        tolto = _FORMA_SOCIETARIA.sub("", corto).rstrip(" .,")
        if tolto == corto:
            break
        corto = tolto
    corto = _buono(corto)
    if corto and corto != nome:
        uscita.append(corto)
    return uscita


def versione_exe(percorso):
    """{'CompanyName': ..., 'ProductName': ...} dalle informazioni sulla versione di un eseguibile Windows (lette
    come dati con le funzioni di Windows: il programma non parte). Dizionario vuoto se non ci sono."""
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
    """Gli eseguibili del gioco nella sua cartella (non installatori, crash reporter e simili), i piu' grandi prima."""
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
    """I nomi da non tradurre per il gioco nella cartella (vedi sopra), senza doppioni. Non solleva errori: un nome
    che non si riesce a leggere si salta."""
    trovati = []
    try:
        from . import lingua_unity
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
    except Exception:  # noqa: BLE001 - Steam assente o illeggibile: si fa senza
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
    """Le parole scritte in minuscolo nei testi: un nome di una sola parola che c'e' anche cosi' ('Control', 'mine')
    e' una parola normale del gioco, e va tradotta."""
    parole = set()
    for t in testi:
        if isinstance(t, str):
            parole.update(re.findall(r"\b[^\W\d_A-Z]+\b", t))
    return parole


def da_proteggere(nomi, comuni):
    """I nomi del profilo da proteggere davvero: quelli di piu' parole sempre, quelli di una parola solo se non
    compaiono anche in minuscolo nei testi del gioco (vedi parole_comuni)."""
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
