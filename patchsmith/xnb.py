"""Lettura dei file .xnb di XNA/FNA/MonoGame (sola lettura, libreria standard).

Per ora serve a sapere quali caratteri sanno disegnare i font (SpriteFont): in XNA un carattere che
manca nel font fa andare in crash il gioco (ArgumentException in DrawString/MeasureString), a meno che
il font abbia un carattere predefinito. Quindi una traduzione deve usare solo caratteri presenti.

Formato (XNB versione 5, non compresso): "XNB" + piattaforma + versione + flag + dimensione totale,
elenco dei lettori di tipo, risorse condivise, poi l'oggetto principale.
"""
import struct


class ErroreXnb(ValueError):
    pass


def _leggi_7bit(dati, pos):
    valore = spostamento = 0
    while True:
        b = dati[pos]
        pos += 1
        valore |= (b & 0x7F) << spostamento
        if not b & 0x80:
            return valore, pos
        spostamento += 7
        if spostamento > 35:
            raise ErroreXnb("intero a 7 bit non valido")


def _leggi_stringa(dati, pos):
    n, pos = _leggi_7bit(dati, pos)
    return dati[pos:pos + n].decode("utf-8"), pos + n


def _leggi_char(dati, pos):
    """BinaryReader.ReadChar: un carattere in UTF-8 (1-4 byte)."""
    b = dati[pos]
    n = 1 if b < 0x80 else 2 if b >> 5 == 6 else 3 if b >> 4 == 14 else 4
    return dati[pos:pos + n].decode("utf-8"), pos + n


def intestazione(dati):
    """(piattaforma, versione, flags, lettori [(nome, versione)], posizione dell'oggetto principale)."""
    if dati[:3] != b"XNB":
        raise ErroreXnb("non e' un file XNB")
    piattaforma, versione, flags = chr(dati[3]), dati[4], dati[5]
    dimensione = struct.unpack_from("<I", dati, 6)[0]
    if dimensione != len(dati):
        raise ErroreXnb("dimensione dichiarata diversa da quella del file")
    if flags & 0xC0:
        raise ErroreXnb("XNB compresso: non ancora supportato")
    pos = 10
    n, pos = _leggi_7bit(dati, pos)
    lettori = []
    for _ in range(n):
        nome, pos = _leggi_stringa(dati, pos)
        ver = struct.unpack_from("<i", dati, pos)[0]
        pos += 4
        lettori.append((nome, ver))
    condivise, pos = _leggi_7bit(dati, pos)
    if condivise:
        raise ErroreXnb("risorse condivise non supportate")
    return piattaforma, versione, flags, lettori, pos


def _nome_breve(lettore):
    return lettore.split(",")[0].split("`")[0].rsplit(".", 1)[-1]


def leggi_spritefont(dati):
    """SpriteFont -> {"caratteri": [...], "predefinito": carattere o None, "interlinea", "spaziatura"}."""
    _, _, _, lettori, pos = intestazione(dati)

    def oggetto(pos, atteso):
        indice, pos = _leggi_7bit(dati, pos)
        if indice == 0:
            raise ErroreXnb("oggetto nullo inatteso")
        nome = lettori[indice - 1][0]
        if atteso not in nome:
            raise ErroreXnb("atteso %s, trovato %s" % (atteso, nome))
        return pos

    pos = oggetto(pos, "SpriteFontReader")
    pos = oggetto(pos, "Texture2DReader")
    _formato, larghezza, altezza, livelli = struct.unpack_from("<iIII", dati, pos)
    pos += 16
    for _ in range(livelli):
        n = struct.unpack_from("<I", dati, pos)[0]
        pos += 4 + n
    for _ in range(2):  # rettangoli dei glifi e ritagli
        pos = oggetto(pos, "ListReader")
        n = struct.unpack_from("<I", dati, pos)[0]
        pos += 4 + 16 * n
    pos = oggetto(pos, "ListReader")
    n = struct.unpack_from("<I", dati, pos)[0]
    pos += 4
    caratteri = []
    for _ in range(n):
        c, pos = _leggi_char(dati, pos)
        caratteri.append(c)
    interlinea, spaziatura = struct.unpack_from("<if", dati, pos)
    pos += 8
    pos = oggetto(pos, "ListReader")
    n = struct.unpack_from("<I", dati, pos)[0]
    pos += 4 + 12 * n
    ha_predefinito = dati[pos]
    pos += 1
    predefinito = None
    if ha_predefinito:
        predefinito, pos = _leggi_char(dati, pos)
    if pos != len(dati):
        raise ErroreXnb("SpriteFont non letto fino in fondo (%d byte avanzati)" % (len(dati) - pos))
    return {"caratteri": caratteri, "predefinito": predefinito, "interlinea": interlinea,
            "spaziatura": spaziatura, "texture": (larghezza, altezza)}


def tipo_principale(dati):
    """Nome breve del lettore dell'oggetto principale (es. 'SpriteFontReader', 'Texture2DReader')."""
    _, _, _, lettori, pos = intestazione(dati)
    indice, _ = _leggi_7bit(dati, pos)
    return _nome_breve(lettori[indice - 1][0]) if indice else None
