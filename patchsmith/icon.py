"""Icona di PatchSmith: un martello sopra una cartella (la cartella del gioco, dove PatchSmith interviene).

Disegnata qui, senza file scaricati: eseguito da solo (patchsmith/icon.py) rifa' le immagini in patchsmith/icon/
(PNG di ogni lato e PatchSmith.ico), che la finestra carica all'avvio (applica), e PatchSmith.exe (eseguibile). Le
forme sono descritte su un quadrato di lato 1 e ogni pixel prende il colore medio dei suoi sottopunti (bordi morbidi,
trasparenza intorno).
"""
import math
import os
import struct
import subprocess
import sys
import zlib

CARTELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon")
PROGRAMMA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # la cartella con PatchSmith.pyw
LATI = (16, 24, 32, 48, 64, 128, 256)

CARTELLA_DAVANTI, CARTELLA_DIETRO, CARTELLA_BORDO = (0xff, 0xc8, 0x45), (0xe0, 0x9a, 0x12), (0x9a, 0x62, 0x08)
TESTA, MANICO, CONTORNO = (0xdf, 0xe5, 0xec), (0x9b, 0x5b, 0x2a), (0x1f, 0x29, 0x37)

# il martello: manico da A verso l'alto a destra (45 gradi), testa di traverso in cima
_A = (0.46, 0.90)
_D = (math.sqrt(0.5), -math.sqrt(0.5))  # lungo il manico
_N = (-_D[1], _D[0])  # di traverso
MANICO_FINE, MEZZA_LARGHEZZA = 0.38, 0.048
TESTA_DA, TESTA_A, MEZZA_TESTA = 0.38, 0.52, 0.15
SPESSORE_CONTORNO = 0.035


def _arrotondato(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _cartella(x, y, margine=0.0):
    """'davanti' (la parte bassa, piu' chiara), 'dietro' (con la linguetta in alto a sinistra) o None; con margine
    negativo le forme si restringono (quello che resta fuori e' il bordo)."""
    m = margine
    if _arrotondato(x, y, 0.06 - m, 0.34 - m, 0.90 + m, 0.84 + m, 0.06 + m):
        return "davanti"
    if _arrotondato(x, y, 0.06 - m, 0.14 - m, 0.42 + m, 0.32 + m, 0.05 + m) or \
            _arrotondato(x, y, 0.06 - m, 0.22 - m, 0.90 + m, 0.84 + m, 0.06 + m):
        return "dietro"
    return None


def _martello(x, y, margine=0.0):
    """'testa', 'manico' o None; con margine le forme si allargano (per il contorno)."""
    t = (x - _A[0]) * _D[0] + (y - _A[1]) * _D[1]
    s = (x - _A[0]) * _N[0] + (y - _A[1]) * _N[1]
    if abs(s) <= MEZZA_TESTA + margine:
        # la testa: dall'altra parte del blocco che batte si assottiglia (la penna)
        mezzo_spessore = (TESTA_A - TESTA_DA) / 2 * (1 - 0.6 * max(0.0, -s / MEZZA_TESTA))
        if abs(t - (TESTA_DA + TESTA_A) / 2) <= mezzo_spessore + margine:
            return "testa"
    if 0 <= t <= MANICO_FINE and abs(s) <= MEZZA_LARGHEZZA + margine:
        return "manico"
    if t < 0 and math.hypot(t, s) <= MEZZA_LARGHEZZA + margine:  # fondo del manico arrotondato
        return "manico"
    return None


def _colore(x, y):
    """(r, g, b, a) del punto (x, y) del quadrato di lato 1."""
    parte = _martello(x, y)
    if parte:
        return (TESTA if parte == "testa" else MANICO) + (255,)
    if _martello(x, y, SPESSORE_CONTORNO):
        return CONTORNO + (255,)
    parte = _cartella(x, y)
    if parte:
        dentro = _cartella(x, y, -SPESSORE_CONTORNO)
        if parte == "davanti" and dentro == "davanti":
            return CARTELLA_DAVANTI + (255,)
        if parte == "dietro" and dentro:
            return CARTELLA_DIETRO + (255,)
        return CARTELLA_BORDO + (255,)
    return (0, 0, 0, 0)


def disegna(lato):
    """Pixel RGBA (bytes, riga per riga) dell'icona di 'lato' pixel."""
    passi = max(4, 64 // lato)  # sottopunti per lato di un pixel: di piu' per le icone piccole
    righe = []
    for py in range(lato):
        riga = bytearray()
        for px in range(lato):
            somma = [0, 0, 0, 0]
            for sy in range(passi):
                for sx in range(passi):
                    r, g, b, a = _colore((px + (sx + 0.5) / passi) / lato, (py + (sy + 0.5) / passi) / lato)
                    somma[0] += r * a
                    somma[1] += g * a
                    somma[2] += b * a
                    somma[3] += a
            if somma[3]:
                riga += bytes((round(somma[0] / somma[3]), round(somma[1] / somma[3]), round(somma[2] / somma[3]),
                               round(somma[3] / passi ** 2)))
            else:
                riga += b"\0\0\0\0"
        righe.append(bytes(riga))
    return righe


def png(lato):
    """L'icona di 'lato' pixel come file PNG (RGBA)."""
    def blocco(tipo, dati):
        return struct.pack(">I", len(dati)) + tipo + dati + struct.pack(">I", zlib.crc32(tipo + dati) & 0xffffffff)
    grezzo = b"".join(b"\0" + riga for riga in disegna(lato))
    return (b"\x89PNG\r\n\x1a\n" + blocco(b"IHDR", struct.pack(">IIBBBBB", lato, lato, 8, 6, 0, 0, 0))
            + blocco(b"IDAT", zlib.compress(grezzo, 9)) + blocco(b"IEND", b""))


def ico(immagini):
    """File .ico di Windows con le immagini PNG date ({lato: png})."""
    testa = struct.pack("<HHH", 0, 1, len(immagini))
    posizione, voci, dati = 6 + 16 * len(immagini), b"", b""
    for lato, contenuto in sorted(immagini.items()):
        voci += struct.pack("<BBBBHHII", lato % 256, lato % 256, 0, 0, 1, 32, len(contenuto), posizione)
        posizione += len(contenuto)
        dati += contenuto
    return testa + voci + dati


def scrivi(cartella=CARTELLA):
    """Rifa' le immagini dell'icona: <lato>.png per ogni lato e PatchSmith.ico."""
    os.makedirs(cartella, exist_ok=True)
    immagini = {lato: png(lato) for lato in LATI}
    for lato, contenuto in immagini.items():
        with open(os.path.join(cartella, "%d.png" % lato), "wb") as f:
            f.write(contenuto)
    with open(os.path.join(cartella, "PatchSmith.ico"), "wb") as f:
        f.write(ico({k: v for k, v in immagini.items() if k <= 256}))
    return immagini


def applica(radice):
    """L'icona sulla finestra e su tutte quelle che si aprono dopo (finestre di dialogo comprese). Senza le immagini
    (cartella icona/ mancante) resta quella di Tk: non e' un errore."""
    import tkinter as tk
    immagini = []
    for lato in LATI:
        percorso = os.path.join(CARTELLA, "%d.png" % lato)
        if lato <= 128 and os.path.isfile(percorso):  # (oltre 128 pixel Windows non le usa per le finestre)
            immagini.append(tk.PhotoImage(master=radice, file=percorso))
    if immagini:
        radice.iconphoto(True, *immagini)
        radice._icona_patchsmith = immagini  # (le immagini di Tk vanno tenute, se no spariscono)
    return bool(immagini)


def _csc():
    """Compilatore C# di .NET Framework 4 (c'e' in ogni Windows 10 e 11; lo usa anche adapter_unity)."""
    windows = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    for quadro in ("Framework64", "Framework"):
        p = os.path.join(windows, "Microsoft.NET", quadro, "v4.0.30319", "csc.exe")
        if os.path.exists(p):
            return p
    return None


def eseguibile(cartella=PROGRAMMA):
    """PatchSmith.exe accanto a PatchSmith.pyw, con questa icona dentro: il programma su cui fare doppio clic (il .pyw
    ha per forza l'icona di Python, che Windows da' a tutto il tipo di file). E' il piccolo avvio di launcher.cs, che
    apre PatchSmith.pyw con il Python installato. Si compila (compilatore C# di Windows, niente da scaricare) solo se manca
    o se e' piu' vecchio di launcher.cs o dell'icona. True se c'e' (gia' o adesso)."""
    sorgente = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.cs")
    icona = os.path.join(CARTELLA, "PatchSmith.ico")
    destinazione = os.path.join(cartella, "PatchSmith.exe")
    if not sys.platform.startswith("win") or not (os.path.isfile(sorgente) and os.path.isfile(icona)):
        return False
    try:
        if os.path.getmtime(destinazione) >= max(os.path.getmtime(sorgente), os.path.getmtime(icona)):
            return True
    except OSError:
        pass
    csc = _csc()
    if not csc:
        return os.path.isfile(destinazione)
    temporaneo = os.path.join(cartella, "PatchSmith.nuovo.exe")  # poi rinominato: mai un .exe a meta'
    comando = [csc, "-nologo", "-target:winexe", "-optimize", "-codepage:65001", "-win32icon:" + icona,
               "-r:System.Windows.Forms.dll", "-out:" + temporaneo, sorgente]
    try:
        esito = subprocess.run(comando, capture_output=True, timeout=120, creationflags=0x08000000)
        if esito.returncode == 0 and os.path.isfile(temporaneo):
            os.replace(temporaneo, destinazione)
    except (OSError, subprocess.SubprocessError):
        pass
    if os.path.isfile(temporaneo):
        try:
            os.remove(temporaneo)
        except OSError:
            pass
    return os.path.isfile(destinazione)


if __name__ == "__main__":
    scrivi()
    print("Immagini dell'icona in", CARTELLA)
    if eseguibile():
        print("PatchSmith.exe in", PROGRAMMA)
