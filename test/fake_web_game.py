"""Fake web game (an index.html page and a JSON file of English texts) for the tests of the interface, the command
line and safety: it is created in a test folder, no real game.

Usage from the tests: fake_web_game.create(folder) -> relative path of the JSON file of the texts.
"""
import json
import os

TEXTS_FILE = "data/testi.json"

TEXTS = {
    "title": "Lantern Keeper",
    "menu": {
        "start": "Start a new game",
        "continue": "Continue your journey",
        "options": "Options and settings",
        "quit": "Quit to the desktop",
    },
    "dialoghi": [
        {"id": 1, "text": "Welcome back, keeper. The lanterns are fading again."},
        {"id": 2, "text": "Bring the oil from the old mill before the night falls."},
        {"id": 3, "text": "The bridge is broken: you need a rope and two planks."},
        {"id": 4, "text": "Thank you! The village will remember your kindness."},
        {"id": 5, "text": "Press any key to light the lantern near the gate."},
        {"id": 6, "text": "Your backpack is full. Drop something before picking this up."},
        {"id": 7, "text": "A cold wind blows from the mountains tonight."},
        {"id": 8, "text": "The merchant sells better wicks, but they cost more coins."},
        {"id": 9, "text": "You found a silver key under the stone."},
        {"id": 10, "text": "The door is locked. Maybe someone in the village has the key."},
        {"id": 11, "text": "Rest here to save your progress and recover your strength."},
        {"id": 12, "text": "The last lantern is lit. The night is finally over."},
    ],
}

PAGE = """<!doctype html>
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

CODE = """document.title = "Lantern Keeper";
"""


def create(folder):
    os.makedirs(os.path.join(folder, "data"), exist_ok=True)
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(PAGE)
    with open(os.path.join(folder, "gioco.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write(CODE)
    with open(os.path.join(folder, *TEXTS_FILE.split("/")), "w", encoding="utf-8", newline="\n") as f:
        json.dump(TEXTS, f, ensure_ascii=False, indent=2)
    return TEXTS_FILE
