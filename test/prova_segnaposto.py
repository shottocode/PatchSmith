"""Test della protezione dei segnaposto e dei codici di gioco (testo.proteggi), comune a tutti i motori.

Uso: python -I test/prova_segnaposto.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import testo  # noqa: E402

CASI = [
    # (testo, segnaposto attesi)
    ("Press [Input:12] to jump\\nand {0} times", ["[Input:12]", "\\n", "{0}"]),  # \n scritto come testo
    ("[Button:LeftStick] to navigate", ["[Button:LeftStick]"]),
    ("[x] heroes slain by [y]", ["[x]", "[y]"]),
    ("Line one\\nLine two", ["\\n"]),  # la parola dopo \n resta da tradurre
    ("\\C[3]Red\\C[0] text \\N[1] and \\n[2] and \\G", ["\\C[3]", "\\C[0]", "\\N[1]", "\\n[2]", "\\G"]),  # RPG Maker
    ("\\nc<Harold>Hello there!", ["\\nc", "<Harold>"]),  # nome nel riquadro (Yanfly)
    ("\\nr<\\N[2]>Hi", ["\\nr", "<\\N[2]>"]),
    ("Saved to C:\\new\\test\\readme.txt ok", ["C:\\new\\test\\readme.txt"]),  # percorso intero
    ("%APPDATA%\\roaming\\temp here", ["%APPDATA%\\roaming\\temp"]),
    ("Option A:\\nNext", ["\\n"]),
    ("Can be interrupted;-;Knocks back", [";-;"]),
    ("Hello %s, you have %d coins", ["%s", "%d"]),
    ("[Note: read me] and [12:30]", []),  # testo tra parentesi con spazi o numeri: si traduce
    ("Hey /name/. Got /curr/ dosh", ["/name/", "/curr/"]),  # codici tra barre (GameMaker/Dokimon)
    ("worldly governors/n/worldly governors", ["/n/"]),
    ("either/or and 1/2/3", []),  # barre normali: si traducono
    ("both gain [yellow1]{0}[/c] HP", ["[yellow1]", "{0}", "[/c]"]),  # tag di Scribble (GameMaker), anche la chiusura
    ("[ftAlkhemikalSh]A game by[/f] and [/] end", ["[ftAlkhemikalSh]", "[/f]", "[/]"]),
]


def main():
    ok = 0
    for s, atteso in CASI:
        protetto, trovati = testo.proteggi(s)
        rimesso = testo.ripristina_segnaposto(protetto, trovati)
        esito = trovati == atteso and rimesso == s
        ok += esito
        print(("OK    " if esito else "ERRORE") + "  %r -> %r %s" % (s, protetto, trovati))
    print("\nRISULTATO: %d/%d controlli superati" % (ok, len(CASI)))
    return ok == len(CASI)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main() else 1)
