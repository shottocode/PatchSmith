"""Lettura di data.win di GameMaker (sola lettura, libreria standard): pezzi, stringhe, font.

data.win = "FORM" + dimensione + pezzi ("GEN8", "STRG", "FONT", ...), ognuno nome + dimensione + contenuto.
I riferimenti tra pezzi sono posizioni assolute nel file; le stringhe sono lunghezza (uint32) + UTF-8 + 0,
e i riferimenti puntano al testo (cioe' alla lunghezza + 4).

I font di GameMaker hanno i caratteri "cotti" al momento della compilazione: un carattere che non c'e'
viene disegnato come un quadratino (o non compare). Per questo una traduzione deve usare i caratteri dei font.
"""
import os
import re
import struct

NOMI_DATI = ("data.win", "game.unx", "game.ios", "game.droid")


class ErroreGameMaker(ValueError):
    pass


def pezzi(dati):
    """{nome: (inizio contenuto, dimensione)} dei pezzi di un data.win."""
    if len(dati) < 8 or dati[:4] != b"FORM":
        raise ErroreGameMaker("non e' un data.win di GameMaker (manca FORM)")
    fine = 8 + struct.unpack_from("<I", dati, 4)[0]
    if fine > len(dati):
        raise ErroreGameMaker("data.win troncato")
    uscita, p = {}, 8
    while p + 8 <= fine:
        nome = dati[p:p + 4].decode("ascii", "replace")
        dim = struct.unpack_from("<I", dati, p + 4)[0]
        if p + 8 + dim > fine:
            raise ErroreGameMaker("pezzo %s oltre la fine del file" % nome)
        uscita[nome] = (p + 8, dim)
        p += 8 + dim
    return uscita


def stringa(dati, puntatore):
    """Stringa di GameMaker dal riferimento (che punta al testo, preceduto dalla lunghezza)."""
    if not 4 <= puntatore <= len(dati) - 1:
        raise ErroreGameMaker("riferimento a stringa non valido")
    n = struct.unpack_from("<I", dati, puntatore - 4)[0]
    if puntatore + n >= len(dati) or dati[puntatore + n] != 0:
        raise ErroreGameMaker("stringa non valida")
    return dati[puntatore:puntatore + n].decode("utf-8", "replace")


def _glifi(dati, inizio, fine, p):
    """Prova a leggere in p un elenco di puntatori a glifi; restituisce i caratteri o None.

    L'elenco e' valido solo se i glifi cominciano subito dopo i puntatori, in ordine crescente."""
    if p + 4 > fine:
        return None
    n = struct.unpack_from("<I", dati, p)[0]
    if not 0 < n <= 70000 or p + 4 + 4 * n > fine:
        return None
    puntatori = struct.unpack_from("<%dI" % n, dati, p + 4)
    if puntatori[0] != p + 4 + 4 * n:
        return None
    caratteri, precedente, ultimo = [], -1, 0
    for q in puntatori:
        if not inizio <= q <= fine - 16 or q <= ultimo:
            return None
        c, x, y, w, h = struct.unpack_from("<HHHHH", dati, q)
        if w > 4096 or h > 4096 or c <= precedente:  # i glifi sono in ordine di carattere
            return None
        caratteri.append(chr(c))
        precedente, ultimo = c, q
    return caratteri


def font(dati):
    """([{'nome', 'caratteri'}], quanti font non si sono potuti leggere)."""
    tabella = pezzi(dati)
    if "FONT" not in tabella:
        return [], 0
    inizio, dim = tabella["FONT"]
    fine = inizio + dim
    if dim < 4:
        raise ErroreGameMaker("pezzo FONT non valido")
    n = struct.unpack_from("<I", dati, inizio)[0]
    if n > 10000 or dim < 4 + 4 * n:
        raise ErroreGameMaker("pezzo FONT non valido")
    uscita, illeggibili = [], 0
    for k in range(n):
        p = struct.unpack_from("<I", dati, inizio + 4 + 4 * k)[0]
        try:
            if not inizio <= p < fine - 4:
                raise ErroreGameMaker("font fuori dal pezzo FONT")
            nome = stringa(dati, struct.unpack_from("<I", dati, p)[0])
        except (ErroreGameMaker, struct.error):
            illeggibili += 1
            continue
        # i campi dopo il nome cambiano con la versione di GameMaker: cerco l'elenco dei glifi
        for spostamento in range(40, 96, 4):
            caratteri = _glifi(dati, inizio, fine, p + spostamento)
            if caratteri:
                uscita.append({"nome": nome, "caratteri": caratteri})
                break
        else:
            illeggibili += 1
    return uscita, illeggibili


def leggi(percorso):
    with open(percorso, "rb") as f:
        return f.read()


def e_dati(percorso):
    """True se il file e' un data.win (o game.unx...) di GameMaker."""
    try:
        with open(percorso, "rb") as f:
            return f.read(4) == b"FORM"
    except OSError:
        return False


def radici(cartella, profondita=2):
    """Cartelle (fino a 'profondita' livelli sotto quella scelta) che contengono un data.win di GameMaker."""
    trovate = []
    base = os.path.abspath(cartella)
    for radice, dirs, files in os.walk(base):
        livello = os.path.relpath(radice, base).count(os.sep) + (0 if radice == base else 1)
        if livello >= profondita:
            dirs[:] = []
        dirs.sort()
        if any(f.lower() in NOMI_DATI and e_dati(os.path.join(radice, f)) for f in files):
            trovate.append(radice)
    return trovate


def sprite(dati):
    """[{'nome', 'fotogrammi'}] degli sprite (pezzo SPRT). Formato di GameMaker Studio 1 e 2 (con il segno -1
    dopo i 14 campi fissi); gli sprite che non si riescono a leggere sono saltati."""
    tabella = pezzi(dati)
    if "SPRT" not in tabella:
        return []
    inizio, dim = tabella["SPRT"]
    fine = inizio + dim
    n = struct.unpack_from("<I", dati, inizio)[0]
    if n > 100000 or dim < 4 + 4 * n:
        raise ErroreGameMaker("pezzo SPRT non valido")
    uscita = []
    for k in range(n):
        p = struct.unpack_from("<I", dati, inizio + 4 + 4 * k)[0]
        if not inizio <= p <= fine - 64:
            continue
        try:
            nome = stringa(dati, struct.unpack_from("<I", dati, p)[0])
            q = p + 56
            segno = struct.unpack_from("<i", dati, q)[0]
            if segno == -1:  # GameMaker Studio 2: versione, tipo, velocita', [campi della versione], fotogrammi
                versione, tipo = struct.unpack_from("<II", dati, q + 4)
                if tipo != 0:  # sprite vettoriali o spine: niente fotogrammi normali
                    continue
                q += 20 + (4 if versione >= 2 else 0) + (4 if versione >= 3 else 0)
                fotogrammi = struct.unpack_from("<I", dati, q)[0]
            else:
                fotogrammi = segno
            if 0 <= fotogrammi <= 10000:
                uscita.append({"nome": nome, "fotogrammi": fotogrammi})
        except (ErroreGameMaker, struct.error):
            continue
    return uscita


def nomi_del_gioco(dati):
    """Nomi del gioco scritti in GEN8 (nome del file, nome, nome visualizzato): servono a trovare la cartella dei
    salvataggi in %LOCALAPPDATA%."""
    tabella = pezzi(dati)
    if "GEN8" not in tabella:
        return []
    inizio, dim = tabella["GEN8"]
    nomi = []
    for spostamento in (4, 40):
        try:
            nomi.append(stringa(dati, struct.unpack_from("<I", dati, inizio + spostamento)[0]))
        except (ErroreGameMaker, struct.error):
            continue
    return [x for x in dict.fromkeys(nomi) if x]


def stringhe(dati):
    """Tutte le stringhe del pezzo STRG (giochi compilati per la macchina virtuale)."""
    tabella = pezzi(dati)
    if "STRG" not in tabella:
        return []
    inizio, dim = tabella["STRG"]
    n = struct.unpack_from("<I", dati, inizio)[0]
    if n > 1000000 or dim < 4 + 4 * n:
        raise ErroreGameMaker("pezzo STRG non valido")
    uscita = []
    for k in range(n):
        p = struct.unpack_from("<I", dati, inizio + 4 + 4 * k)[0]
        try:
            uscita.append(stringa(dati, p + 4))
        except (ErroreGameMaker, struct.error):
            continue
    return uscita


def compilato_yyc(dati):
    """True se il gioco e' compilato in codice nativo (YYC): data.win non ha il codice (pezzo CODE), che sta
    nell'eseguibile insieme alle stringhe che usa."""
    return "CODE" not in pezzi(dati)


def eseguibili(cartella):
    """Gli .exe del gioco nella cartella di data.win (senza disinstallatori e simili)."""
    try:
        nomi = sorted(os.listdir(cartella))
    except OSError:
        return []
    return [os.path.join(cartella, n) for n in nomi if n.lower().endswith(".exe")
            and not re.match(r"(?i)unins|setup|install|crash|launcher|redist|vc_?redist", n)]


_PEZZO_TESTO = re.compile(rb"(?<=\x00)[^\x00]{20,2400}(?=\x00)")


def _candidate(testi, lunghezze):
    """Le stringhe che possono essere la mappa di un font a sprite: tanti caratteri quanti i fotogrammi di uno
    sprite con 'font' nel nome, tutti diversi, con almeno 20 lettere a-z nell'ordine dell'alfabeto."""
    uscita = []
    for t in testi:
        if len(t) not in lunghezze or len(set(t)) != len(t):
            continue
        lettere = [c for c in t if "a" <= c <= "z"]
        if len(lettere) >= 20 and lettere == sorted(lettere):
            uscita.append(t)
    return uscita


def mappe_font_sprite(cartella, dati=None):
    """[(nome dello sprite, mappa dei caratteri)] dei font a sprite (font_add_sprite_ext): i giochi che disegnano
    il testo con immagini di lettere invece che con un font. La mappa e' una stringa con un carattere per
    fotogramma: nei giochi compilati per la macchina virtuale sta nel pezzo STRG, in quelli YYC nell'eseguibile."""
    if dati is None:
        percorso = next((os.path.join(cartella, n) for n in NOMI_DATI if os.path.exists(os.path.join(cartella, n))),
                        None)
        if not percorso:
            return []
        dati = leggi(percorso)
    per_lunghezza = {}
    for sp in sprite(dati):
        if "font" in sp["nome"].lower() and sp["fotogrammi"] >= 20:
            per_lunghezza.setdefault(sp["fotogrammi"], []).append(sp["nome"])
    if not per_lunghezza:
        return []
    testi = stringhe(dati)
    if compilato_yyc(dati):
        for exe in eseguibili(cartella):
            try:
                with open(exe, "rb") as f:
                    contenuto = f.read()
            except OSError:
                continue
            for m in _PEZZO_TESTO.finditer(contenuto):
                try:
                    testi.append(m.group().decode("utf-8"))
                except UnicodeDecodeError:
                    continue
    trovate = []
    for mappa in dict.fromkeys(_candidate(testi, per_lunghezza)):
        for nome in per_lunghezza[len(mappa)]:
            trovate.append((nome, mappa))
    return trovate


def cartella_maschera(cartella, relativa, estensione, dati=None):
    """True se il gioco cerca da solo i file '<relativa>/*<estensione>' (es. 'local/*.json'): allora un file di
    lingua nuovo messo li' diventa una lingua selezionabile nel gioco. Si cerca la maschera tra le stringhe del
    gioco (pezzo STRG, o l'eseguibile se e' compilato YYC)."""
    maschere = {("%s/*%s" % (relativa, estensione)).encode("utf-8"), ("%s\\*%s" % (relativa, estensione)).encode("utf-8")}
    if dati is None:
        percorso = next((os.path.join(cartella, n) for n in NOMI_DATI if os.path.exists(os.path.join(cartella, n))),
                        None)
        if not percorso:
            return False
        dati = leggi(percorso)
    try:
        if any(m.decode("utf-8") in stringhe(dati) for m in maschere):
            return True
        if not compilato_yyc(dati):
            return False
    except (ErroreGameMaker, struct.error):
        return False
    for exe in eseguibili(cartella):
        try:
            with open(exe, "rb") as f:
                contenuto = f.read()
        except OSError:
            continue
        if any(b"\x00" + m + b"\x00" in contenuto for m in maschere):
            return True
    return False
