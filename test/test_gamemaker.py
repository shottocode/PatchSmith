"""Test of the GameMaker adapter with a fake game (localization files + minimal date.win with two fonts).

Usage: python -I test/test_gamemaker.py <empty_temp_folder>
The test's game profiles and translation memories stay in the temporary folder (not in PatchSmith's).
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (adapter_gamemaker, adapter_lines, auto as automatic, characters as chars,
                        gamemaker, project)  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


UI = ("// commento del traduttore: non si tocca\r\n"
      "0 Fight\r\n1 Switch\r\n\r\n4 What should \r\n5  do next?\r\n29  \r\n"
      "30 Hey /name/, you have /curr/ dosh/n/Spend it!\r\n31 100\r\n")
NPC = ("0\n...\n.\n\n1\nWhoa! You have a Dokimon with you??\nBe careful!\n.\n\n"
       "4 \nDon't mess with us!\n.\nWell, I suppose I lost..\n.\n\n.\n")
FR_UI = "0 Attaquer\r\n1 Changer\r\n2 Capturer\r\n3 Fuir\r\n4 Qu'est ce que \r\n5  doit faire ?\r\n"


def data_win(fonts):
    """Minimal FORM with STRG (names) and FONT (glyphs), in the format read by patchsmith.gamemaker."""
    strg_body, positions = bytearray(), []
    for name, _ in fonts:
        positions.append(len(strg_body) + 4)
        strg_body += struct.pack("<I", len(name)) + name.encode() + b"\x00"
    strg_start = 8 + 8  # FORM(8) + STRG header(8)
    strg = b"STRG" + struct.pack("<I", len(strg_body)) + bytes(strg_body)
    font_start = 8 + len(strg) + 8
    body = bytearray(struct.pack("<I", len(fonts)) + b"\x00" * (4 * len(fonts)))
    for k, (_, sec_flags) in enumerate(fonts):
        p = font_start + len(body)
        struct.pack_into("<I", body, 4 + 4 * k, p)
        header = struct.pack("<I", strg_start + positions[k]) + b"\x00" * 48
        glyphs_start = p + len(header) + 4 + 4 * len(sec_flags)
        body += header + struct.pack("<I", len(sec_flags))
        body += b"".join(struct.pack("<I", glyphs_start + 16 * i) for i in range(len(sec_flags)))
        for c in sorted(sec_flags):
            body += struct.pack("<HHHHHhhH", ord(c), 0, 0, 8, 8, 8, 0, 0)
    body += b"\x00" * 32
    font = b"FONT" + struct.pack("<I", len(body)) + bytes(body)
    content = strg + font
    return b"FORM" + struct.pack("<I", len(content)) + content


def fingerprints(folder):
    output = {}
    for root, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            output[os.path.relpath(p, folder)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return output


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    game = os.path.join(base, "Gioco GM")
    os.makedirs(os.path.join(game, "Localization", "french"))
    for rel, text in (("Localization/english_ui.txt", UI), ("Localization/english_npc.txt", NPC),
                       ("Localization/french/ui.txt", FR_UI)):
        open(os.path.join(game, *rel.split("/")), "wb").write(text.encode("utf-8"))
    latin = set(chr(c) for c in range(32, 127)) | set("àèéùÀÈÉÙíóúÍÓÚ")
    open(os.path.join(game, "data.win"), "wb").write(data_win([("fn_testo", latin), ("fn_altro", latin - set("ù")),
                                                                ("fn_numeri", set("0123456789"))]))
    open(os.path.join(game, "Gioco.exe"), "wb").write(b"MZ")
    before = fingerprints(game)

    # line format and block format
    ui = adapter_lines.read_file(os.path.join(game, "Localization", "english_ui.txt"))
    npc = adapter_lines.read_file(os.path.join(game, "Localization", "english_npc.txt"))
    check("file a righe: modo, testi, commento e numeri esclusi", ui.mode == "righe" and
              [t for _, t in ui.units] == ["Fight", "Switch", "What should ", " do next?",
                                           "Hey /name/, you have /curr/ dosh/n/Spend it!"])
    check("file a blocchi: sezioni (anche due per voce) e '.' isolato", npc.mode == "blocchi" and
              [t for _, t in npc.units] == ["Whoa! You have a Dokimon with you??\nBe careful!",
                                            "Don't mess with us!", "Well, I suppose I lost.."])
    check("riscrittura senza modifiche identica (CRLF e LF)", ui.serialize({}) == UI.encode()
              and npc.serialize({}) == NPC.encode())
    try:
        npc.serialize({npc.units[0][0]: "una riga sola"})
        check("blocco con un numero di righe diverso: rifiutato", False)
    except adapter_lines.LinesError:
        check("blocco con un numero di righe diverso: rifiutato", True)

    # rules of the game's parser (Dokimon): separators, terminators, numeric prefixes, slashes
    variants = adapter_lines.LinesFile("0\tFight\r\n1\u3000Switch\r\n2 Catch\r\n".encode("utf-8"))
    check("separatori tab e spazio ideografico riconosciuti e conservati",
              [t for _, t in variants.units] == ["Fight", "Switch", "Catch"]
              and variants.serialize({("riga", 0): "Combatti"}).startswith("0\tCombatti\r\n".encode()))
    closers = adapter_lines.LinesFile("1\nHi there\n. \n2\nBye now\n\uff0e\n3\nOk then\n.\n".encode("utf-8"))
    check("chiusure '. ' e '\uff0e' riconosciute", closers.mode == "blocchi" and len(closers.units) == 3)
    section = npc.units[0]
    check("riga che comincerebbe con un numero (ruberebbe un'altra voce): scartata",
              not adapter_lines.is_valid(section[0], section[1], "100 cose\nAttento!")
              and adapter_lines.is_valid(section[0], section[1], "Wow! Hai un Dokimon??\nAttento!"))
    data, rejected = npc.serialize_robust({section[0]: "Wow!\n.", npc.units[1][0]: "Non scherzare!"})
    check("una traduzione non valida resta in originale senza bloccare le altre",
              rejected == [section[0]] and "Non scherzare!" in data.decode())
    check("barra in piu' -> '-'; codici identici; codice a fine riga chiuso da uno spazio",
              adapter_gamemaker.repair("Speedup Toggle", "Attiva/disattiva accelerazione")
              == "Attiva-disattiva accelerazione"
              and adapter_gamemaker.repair("Hey /name/!", "Ciao!") is None
              and adapter_gamemaker.repair("How is it, /name/?\nOk", "Come va, /name/\nOk") == "Come va, /name/ \nOk")
    check("caratteri di controllo o separatori di riga Unicode: traduzione scartata",
              not adapter_lines.is_valid(("riga", 0), "Fight", "Combatti ora")
              and not adapter_lines.is_valid(("riga", 0), "Fight", "Comb\x0catti")
              and adapter_lines.is_valid(("riga", 0), "Fight", "Combatti\tora"))
    check("lingua dal percorso: codici brevi solo in posizioni sicure",
              adapter_gamemaker.path_language("Localization/english_ui.txt") == "en"
              and adapter_gamemaker.path_language("lang/it/ui.txt") == "it"
              and adapter_gamemaker.path_language("text/edit_items.txt") is None
              and adapter_gamemaker.path_language("data/pt1_dialog.txt") is None
              and adapter_gamemaker.path_language("text/ui_fr.txt") == "fr")

    # file search and fonts
    found = adapter_gamemaker.localization_file(game)
    sources, languages, _ = adapter_gamemaker.choose_sources(found)
    check("file della lingua originale trovati, il francese no", sources == [
        "Localization/english_npc.txt", "Localization/english_ui.txt"] and languages == {"en", "fr"})
    allowed = adapter_gamemaker.font_chars(game)
    check("font: caratteri comuni ai font di testo (il font di sole cifre non conta)",
              allowed is not None and "à" in allowed and "ù" not in allowed and "a" in allowed)
    check("italiano: ì->í, ò->ó, ù->ú se il font non li ha; virgolette semplici",
              chars.adapt_chars("Così può più \u201cciao\u201d", allowed, "it") == 'Cosí puó piú "ciao"')
    check("gamemaker.font: nomi letti",
              [f["nome"] for f in gamemaker.font(open(os.path.join(game, "data.win"), "rb").read())[0]]
              == ["fn_testo", "fn_altro", "fn_numeri"])
    check("italiano: à è restano se il font li ha; l'acuto solo per i o u",
              chars.adapt_chars("è già", set(chars.ASCII) | set("áéíóú"), "it") == "e' gia'")
    # font policy: an ASCII-only font counts; a Korean one without accents does not; an uppercase-only one also covers
    # lowercase
    tests = os.path.join(base, "font")
    os.makedirs(tests)
    korean = set(chr(c) for c in range(32, 127)) | {chr(0xAC00 + i) for i in range(300)}
    uppercase = set(chr(c) for c in range(32, 97)) | set("ÀÈÉÙÍÓÚ")
    cases = [("solo ASCII + accentato", [("a", latin), ("b", set(chr(c) for c in range(32, 127)))],
             lambda a: "a" in a and "è" not in a),
            ("coreano escluso", [("a", latin), ("k", korean)], lambda a: "è" in a),
            ("sole maiuscole", [("a", latin), ("m", uppercase)], lambda a: "a" in a and "è" in a)]
    for name, fonts, expected in cases:
        open(os.path.join(tests, "data.win"), "wb").write(data_win(fonts))
        check("font: " + name, expected(adapter_gamemaker.font_chars(tests) or set()))
    open(os.path.join(tests, "data.win"), "wb").write(b"FORM\x10\x00\x00\x00FONT")
    check("data.win troncato: solo ASCII, nessun errore", adapter_gamemaker.font_chars(tests) == set(chars.ASCII))

    # full flow with the test translator; game profiles and translation memories in the test folder
    os.makedirs(os.path.join(base, "patchsmith", "profiles"))
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    automatic.PROFILES_FOLDER = os.path.join(base, "patchsmith", "profiles")
    automatic.adapter_gamemaker  # noqa: B018 (imported)
    path, completed, _ = automatic.analyze_and_translate(game, "auto", "it", "finto", silent)
    check("analizza e traduci: completato, profilo GameMaker", completed and path
              and '"motore": "gamemaker"' in open(path, encoding="utf-8").read())
    written = open(os.path.join(game, "Localization", "english_ui.txt"), "rb").read().decode()
    check("file tradotto: struttura intatta, spazio iniziale e codici conservati",
              "0 [it] Fight\r\n" in written and "5  [it] do next?\r\n" in written and "/name/" in written
              and "/n/" in written and written.startswith("// commento") and "31 100\r\n" in written)
    check("il file francese non e' toccato",
              fingerprints(game)[os.path.join("Localization", "french", "ui.txt")]
              == before[os.path.join("Localization", "french", "ui.txt")])
    check("riapplica senza rete: ok", automatic.reapply(game, silent, test=True))
    automatic.restore(game, silent)
    check("ripristino: cartella del gioco identica a prima", fingerprints(game) == before)
    project.game_open = lambda folder: True
    check("gioco aperto: non scrivo nulla", not automatic.reapply(game, silent, test=True)
              and fingerprints(game) == before)
    project.game_open = lambda folder: False

    # translated file edited by hand, then reapply and restore: the English original is never lost
    automatic.reapply(game, silent, test=True)
    ui_path = os.path.join(game, "Localization", "english_ui.txt")
    open(ui_path, "ab").write(b"99 Aggiunta a mano\r\n")
    log_lines = []
    automatic.reapply(game, log_lines.append, test=True)
    check("modifica a mano riconosciuta (non scambiata per un aggiornamento del gioco)",
              any("modified by hand" in r for r in log_lines)
              and not any(f.endswith(".patchsmith-originale.1")
                          for f in os.listdir(os.path.join(game, "Localization"))))
    automatic.restore(game, silent)
    check("modifica a mano + ripristino: l'originale inglese torna al suo posto, identico",
              fingerprints(game) == before)

    # file changed after the translation and never reapplied: restoring does not delete the original
    automatic.reapply(game, silent, test=True)
    open(ui_path, "ab").write(b"98 Cambiato da altri\r\n")
    automatic.restore(game, silent)
    check("file cambiato da altri: backup dell'originale conservato",
              open(ui_path + ".patchsmith-originale", "rb").read() == UI.encode())
    os.remove(ui_path + ".patchsmith-originale")
    os.remove(ui_path + project.INFO_SUFFIX)
    open(ui_path, "wb").write(UI.encode())
    check("... e rimesso a posto a mano la cartella torna identica", fingerprints(game) == before)

    # the game already has Italian: no translation
    os.makedirs(os.path.join(game, "Localization", "italian"))
    open(os.path.join(game, "Localization", "italian", "ui.txt"), "wb").write(
        b"0 Combatti\r\n1 Cambia\r\n2 Cattura\r\n3 Fuggi\r\n4 Cosa deve fare \r\n5  ora?\r\n")
    path, completed, _ = automatic.analyze_and_translate(game, "auto", "it", "finto", silent)
    check("lingua gia' presente nel gioco: niente traduzione", not completed
              and not os.path.exists(os.path.join(game, "Localization", "english_ui.txt.patchsmith-originale")))

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
