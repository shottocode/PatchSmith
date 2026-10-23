"""Generates test/web/index-inline.html: a test page for the injector with the script embedded.

Usage: python -I test/web/make_page.py   (then open index-inline.html in a browser)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from patchsmith import adapter_webui as w  # noqa: E402

DICTIONARY = {
    "Quit Game": "Esci dal gioco", "Continue": "Continua", "Text size": "Dimensione testo",
    "The menu music is playing now. Drag the": "La musica del menu è in riproduzione. Trascina il",
    "Music": "Musica", "Open menu": "Apri menu", "Search…": "Cerca…", "Score": "Punteggio",
    "Sudoku Mansion": "Villa Sudoku", "Room Cleared!": "Stanza completata!",
    "Notes": "Note", "Note": "Nota",  # the translation of one entry is the key of another
    "Large": "Grande", "Start Game": "Inizia partita", "Save & Quit": "Salva ed esci",
    "Delete": "Elimina", "Enter a digit": "Inserisci una cifra",
    "設定": "Impostazioni", "はい": "Sì",  # 2-character Japanese menus
    "PLAY": "GIOCA",  # one-word button, all upper case
}

PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Sudoku Mansion</title>
  </head>
  <body>
    <h1 id="t"> Continue </h1>
    <p id="frag">The menu music is playing now. Drag the <strong>Music</strong> slider to set its volume live.</p>
    <button id="b" title="Open menu" aria-label="Open menu">Quit Game</button>
    <input id="i" placeholder="Search…">
    <textarea id="ta">Continue</textarea>
    <select id="sel"><option>Large</option><option value="s">Note</option></select>
    <form id="f"><input id="sub" type="submit" name="act" value="Start Game"><input id="btn2" type="button" value="Start Game"></form>
    <p id="notes">Notes</p><p id="note">Note</p><button id="amp">Save &amp; Quit</button>
    <button id="del" class="danger-btn">Delete</button>
    <button id="cjk1">設定</button><button id="cjk2"> はい </button><span id="short">OK</span>
    <button id="play">PLAY</button><button id="ok">OK</button>
    <ul><li class="hotkeys-row"><span class="hotkeys-keys"><kbd id="kbd" class="key">Delete</kbd>
      <button id="keybtn" type="button" class="key key--editable">Delete</button></span>
      <span id="action" class="hotkeys-actions">Enter a digit</span></li></ul>
    <p id="dyn"></p><p id="untouched">Some other text</p>
    <canvas id="c" width="300" height="40"></canvas>
    <script>
      setTimeout(function(){
        document.getElementById('dyn').textContent = 'Room Cleared!';
        document.getElementById('t').firstChild.nodeValue = ' Text size ';
        var n = document.createElement('div'); n.id = 'new';
        n.innerHTML = '<span>Quit Game</span><span>Continue</span><span>Music</span>';
        document.body.appendChild(n);
        document.getElementById('b').setAttribute('title', 'Open menu');
        var ctx = document.getElementById('c').getContext('2d');
        window.__w1 = ctx.measureText('Score').width; window.__w0 = ctx.measureText('Punteggio').width;
        ctx.fillText('Score', 10, 20);
        window.__ready = true;
      }, 50);
    </script>
  </body>
</html>"""


def main():
    script = w.generate_script(DICTIONARY)
    page = w.insert_tag(PAGE).replace(
        w.TAG, "<script>\n" + script + "\n</script>")
    with open(os.path.join(HERE, "index-inline.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print("written", os.path.join(HERE, "index-inline.html"))


if __name__ == "__main__":
    main()
