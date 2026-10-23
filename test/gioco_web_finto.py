"""Gioco web finto (una pagina index.html e un file JSON di testi in inglese) per le prove dell'interfaccia, della
riga di comando e della sicurezza: si crea in una cartella di prova, nessun gioco vero.

Uso dalle prove: gioco_web_finto.crea(cartella) -> percorso relativo del file JSON dei testi.
"""
import json
import os

FILE_TESTI = "data/testi.json"

TESTI = {
    "title": "Lantern Keeper",
    "menu": {
        "start": "Start a new game",
        "continue": "Continue your journey",
        "options": "Options and settings",
        "quit": "Quit to the desktop",
    },
    "dialoghi": [
        {"id": 1, "testo": "Welcome back, keeper. The lanterns are fading again."},
        {"id": 2, "testo": "Bring the oil from the old mill before the night falls."},
        {"id": 3, "testo": "The bridge is broken: you need a rope and two planks."},
        {"id": 4, "testo": "Thank you! The village will remember your kindness."},
        {"id": 5, "testo": "Press any key to light the lantern near the gate."},
        {"id": 6, "testo": "Your backpack is full. Drop something before picking this up."},
        {"id": 7, "testo": "A cold wind blows from the mountains tonight."},
        {"id": 8, "testo": "The merchant sells better wicks, but they cost more coins."},
        {"id": 9, "testo": "You found a silver key under the stone."},
        {"id": 10, "testo": "The door is locked. Maybe someone in the village has the key."},
        {"id": 11, "testo": "Rest here to save your progress and recover your strength."},
        {"id": 12, "testo": "The last lantern is lit. The night is finally over."},
    ],
}

PAGINA = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Lantern Keeper</title>
  </head>
  <body>
    <h1>Lantern Keeper</h1>
    <button>Start a new game</button>
    <button>Options and settings</button>
    <p>Press <kbd>Delete</kbd> to remove a saved game.</p>
    <script src="gioco.js"></script>
  </body>
</html>
"""

CODICE = """document.title = "Lantern Keeper";
"""


def crea(cartella):
    os.makedirs(os.path.join(cartella, "data"), exist_ok=True)
    with open(os.path.join(cartella, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(PAGINA)
    with open(os.path.join(cartella, "gioco.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(CODICE)
    with open(os.path.join(cartella, *FILE_TESTI.split("/")), "w", encoding="utf-8", newline="\n") as f:
        json.dump(TESTI, f, ensure_ascii=False, indent=2)
    return FILE_TESTI
