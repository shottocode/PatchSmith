"""Reading GameMaker's data.win (read-only, standard library): chunks, strings, fonts.

data.win = "FORM" + size + chunks ("GEN8", "STRG", "FONT", ...), each one name + size + content.
References between chunks are absolute offsets in the file; strings are length (uint32) + UTF-8 + 0,
and references point to the text (that is, to the length + 4).

GameMaker fonts have their characters "baked" at compile time: a character that is not there
is drawn as a small square (or does not appear). That is why a translation must use the fonts' characters.
"""
import os
import re
import struct

NOMI_DATI = ("data.win", "game.unx", "game.ios", "game.droid")


class ErroreGameMaker(ValueError):
    pass


def pezzi(dati):
    """{nome: (content start, size)} of the chunks of a data.win."""
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
    """GameMaker string from its reference (which points to the text, preceded by the length)."""
    if not 4 <= puntatore <= len(dati) - 1:
        raise ErroreGameMaker("riferimento a stringa non valido")
    n = struct.unpack_from("<I", dati, puntatore - 4)[0]
    if puntatore + n >= len(dati) or dati[puntatore + n] != 0:
        raise ErroreGameMaker("stringa non valida")
    return dati[puntatore:puntatore + n].decode("utf-8", "replace")


def _glifi(dati, inizio, fine, p):
    """Tries to read a list of glyph pointers at p; returns the characters or None.

    The list is valid only if the glyphs start right after the pointers, in increasing order."""
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
        if w > 4096 or h > 4096 or c <= precedente:  # glyphs are in character order
            return None
        caratteri.append(chr(c))
        precedente, ultimo = c, q
    return caratteri


def font(dati):
    """([{'nome', 'caratteri'}], how many fonts could not be read)."""
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
        # the fields after the name change with the GameMaker version: look for the glyph list
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
    """True if the file is a GameMaker data.win (or game.unx...)."""
    try:
        with open(percorso, "rb") as f:
            return f.read(4) == b"FORM"
    except OSError:
        return False


def radici(cartella, profondita=2):
    """Folders (up to 'profondita' levels below the chosen one) that contain a GameMaker data.win."""
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
    """[{'nome', 'fotogrammi'}] of the sprites (SPRT chunk). GameMaker Studio 1 and 2 format (with the -1 marker
    after the 14 fixed fields); sprites that cannot be read are skipped."""
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
            if segno == -1:  # GameMaker Studio 2: version, type, speed, [version fields], frames
                versione, tipo = struct.unpack_from("<II", dati, q + 4)
                if tipo != 0:  # vector or Spine sprites: no normal frames
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
    """Names of the game written in GEN8 (file name, name, display name): they are used to find the save folder
    in %LOCALAPPDATA%."""
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
    """All the strings of the STRG chunk (games compiled for the virtual machine)."""
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
    """True if the game is compiled to native code (YYC): data.win does not have the code (CODE chunk), which is
    in the executable together with the strings it uses."""
    return "CODE" not in pezzi(dati)


def eseguibili(cartella):
    """The game's .exe files in the data.win folder (without uninstallers and the like)."""
    try:
        nomi = sorted(os.listdir(cartella))
    except OSError:
        return []
    return [os.path.join(cartella, n) for n in nomi if n.lower().endswith(".exe")
            and not re.match(r"(?i)unins|setup|install|crash|launcher|redist|vc_?redist", n)]


_PEZZO_TESTO = re.compile(rb"(?<=\x00)[^\x00]{20,2400}(?=\x00)")


def _candidate(testi, lunghezze):
    """The strings that can be the map of a sprite font: as many characters as the frames of a sprite with 'font'
    in its name, all different, with at least 20 letters a-z in alphabetical order."""
    uscita = []
    for t in testi:
        if len(t) not in lunghezze or len(set(t)) != len(t):
            continue
        lettere = [c for c in t if "a" <= c <= "z"]
        if len(lettere) >= 20 and lettere == sorted(lettere):
            uscita.append(t)
    return uscita


def mappe_font_sprite(cartella, dati=None):
    """[(sprite name, character map)] of the sprite fonts (font_add_sprite_ext): games that draw text with images
    of letters instead of a font. The map is a string with one character per frame: in games compiled for the
    virtual machine it is in the STRG chunk, in YYC ones in the executable."""
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
    """True if the game looks for the '<relativa>/*<estensione>' files by itself (e.g. 'local/*.json'): then a new
    language file put there becomes a selectable language in the game. The mask is searched among the game's
    strings (STRG chunk, or the executable if it is compiled with YYC)."""
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
