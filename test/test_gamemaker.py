"""Test dell'adapter GameMaker con un gioco finto (file di localizzazione + data.win minimo con due font).

Uso: python -I test/test_gamemaker.py <cartella_temporanea_vuota>
Profili e memorie del test restano nella cartella temporanea (non in quella di PatchSmith).
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (adapter_gamemaker, adapter_lines as adapter_righe, auto as automatico, characters as caratteri,
                        gamemaker, project as progetto)  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


UI = ("// commento del traduttore: non si tocca\r\n"
      "0 Fight\r\n1 Switch\r\n\r\n4 What should \r\n5  do next?\r\n29  \r\n"
      "30 Hey /name/, you have /curr/ dosh/n/Spend it!\r\n31 100\r\n")
NPC = ("0\n...\n.\n\n1\nWhoa! You have a Dokimon with you??\nBe careful!\n.\n\n"
       "4 \nDon't mess with us!\n.\nWell, I suppose I lost..\n.\n\n.\n")
FR_UI = "0 Attaquer\r\n1 Changer\r\n2 Capturer\r\n3 Fuir\r\n4 Qu'est ce que \r\n5  doit faire ?\r\n"


def data_win(fonts):
    """FORM minimo con STRG (nomi) e FONT (glifi), nel formato letto da patchsmith.gamemaker."""
    strg_corpo, posizioni = bytearray(), []
    for nome, _ in fonts:
        posizioni.append(len(strg_corpo) + 4)
        strg_corpo += struct.pack("<I", len(nome)) + nome.encode() + b"\x00"
    inizio_strg = 8 + 8  # FORM(8) + intestazione STRG(8)
    strg = b"STRG" + struct.pack("<I", len(strg_corpo)) + bytes(strg_corpo)
    inizio_font = 8 + len(strg) + 8
    corpo = bytearray(struct.pack("<I", len(fonts)) + b"\x00" * (4 * len(fonts)))
    for k, (_, car) in enumerate(fonts):
        p = inizio_font + len(corpo)
        struct.pack_into("<I", corpo, 4 + 4 * k, p)
        intestazione = struct.pack("<I", inizio_strg + posizioni[k]) + b"\x00" * 48
        glifi_inizio = p + len(intestazione) + 4 + 4 * len(car)
        corpo += intestazione + struct.pack("<I", len(car))
        corpo += b"".join(struct.pack("<I", glifi_inizio + 16 * i) for i in range(len(car)))
        for c in sorted(car):
            corpo += struct.pack("<HHHHHhhH", ord(c), 0, 0, 8, 8, 8, 0, 0)
    corpo += b"\x00" * 32
    font = b"FONT" + struct.pack("<I", len(corpo)) + bytes(corpo)
    contenuto = strg + font
    return b"FORM" + struct.pack("<I", len(contenuto)) + contenuto


def impronte(cartella):
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for f in files:
            p = os.path.join(radice, f)
            uscita[os.path.relpath(p, cartella)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return uscita


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    gioco = os.path.join(base, "Gioco GM")
    os.makedirs(os.path.join(gioco, "Localization", "french"))
    for rel, testo in (("Localization/english_ui.txt", UI), ("Localization/english_npc.txt", NPC),
                       ("Localization/french/ui.txt", FR_UI)):
        open(os.path.join(gioco, *rel.split("/")), "wb").write(testo.encode("utf-8"))
    latino = set(chr(c) for c in range(32, 127)) | set("àèéùÀÈÉÙíóúÍÓÚ")
    open(os.path.join(gioco, "data.win"), "wb").write(data_win([("fn_testo", latino), ("fn_altro", latino - set("ù")),
                                                                ("fn_numeri", set("0123456789"))]))
    open(os.path.join(gioco, "Gioco.exe"), "wb").write(b"MZ")
    prima = impronte(gioco)

    # formato a righe e a blocchi
    ui = adapter_righe.leggi(os.path.join(gioco, "Localization", "english_ui.txt"))
    npc = adapter_righe.leggi(os.path.join(gioco, "Localization", "english_npc.txt"))
    controlla("file a righe: modo, testi, commento e numeri esclusi", ui.modo == "righe" and
              [t for _, t in ui.unita] == ["Fight", "Switch", "What should ", " do next?",
                                           "Hey /name/, you have /curr/ dosh/n/Spend it!"])
    controlla("file a blocchi: sezioni (anche due per voce) e '.' isolato", npc.modo == "blocchi" and
              [t for _, t in npc.unita] == ["Whoa! You have a Dokimon with you??\nBe careful!",
                                            "Don't mess with us!", "Well, I suppose I lost.."])
    controlla("riscrittura senza modifiche identica (CRLF e LF)", ui.serializza({}) == UI.encode()
              and npc.serializza({}) == NPC.encode())
    try:
        npc.serializza({npc.unita[0][0]: "una riga sola"})
        controlla("blocco con un numero di righe diverso: rifiutato", False)
    except adapter_righe.ErroreRighe:
        controlla("blocco con un numero di righe diverso: rifiutato", True)

    # regole del parser del gioco (Dokimon): separatori, chiusure, prefissi numerici, barre
    varianti = adapter_righe.FileRighe("0\tFight\r\n1\u3000Switch\r\n2 Catch\r\n".encode("utf-8"))
    controlla("separatori tab e spazio ideografico riconosciuti e conservati",
              [t for _, t in varianti.unita] == ["Fight", "Switch", "Catch"]
              and varianti.serializza({("riga", 0): "Combatti"}).startswith("0\tCombatti\r\n".encode()))
    chiusure = adapter_righe.FileRighe("1\nHi there\n. \n2\nBye now\n\uff0e\n3\nOk then\n.\n".encode("utf-8"))
    controlla("chiusure '. ' e '\uff0e' riconosciute", chiusure.modo == "blocchi" and len(chiusure.unita) == 3)
    sezione = npc.unita[0]
    controlla("riga che comincerebbe con un numero (ruberebbe un'altra voce): scartata",
              not adapter_righe.valida(sezione[0], sezione[1], "100 cose\nAttento!")
              and adapter_righe.valida(sezione[0], sezione[1], "Wow! Hai un Dokimon??\nAttento!"))
    dati, scartate = npc.serializza_robusto({sezione[0]: "Wow!\n.", npc.unita[1][0]: "Non scherzare!"})
    controlla("una traduzione non valida resta in originale senza bloccare le altre",
              scartate == [sezione[0]] and "Non scherzare!" in dati.decode())
    controlla("barra in piu' -> '-'; codici identici; codice a fine riga chiuso da uno spazio",
              adapter_gamemaker.ripara("Speedup Toggle", "Attiva/disattiva accelerazione") == "Attiva-disattiva accelerazione"
              and adapter_gamemaker.ripara("Hey /name/!", "Ciao!") is None
              and adapter_gamemaker.ripara("How is it, /name/?\nOk", "Come va, /name/\nOk") == "Come va, /name/ \nOk")
    controlla("caratteri di controllo o separatori di riga Unicode: traduzione scartata",
              not adapter_righe.valida(("riga", 0), "Fight", "Combatti ora")
              and not adapter_righe.valida(("riga", 0), "Fight", "Comb\x0catti")
              and adapter_righe.valida(("riga", 0), "Fight", "Combatti\tora"))
    controlla("lingua dal percorso: codici brevi solo in posizioni sicure",
              adapter_gamemaker.lingua_del_percorso("Localization/english_ui.txt") == "en"
              and adapter_gamemaker.lingua_del_percorso("lang/it/ui.txt") == "it"
              and adapter_gamemaker.lingua_del_percorso("text/edit_items.txt") is None
              and adapter_gamemaker.lingua_del_percorso("data/pt1_dialog.txt") is None
              and adapter_gamemaker.lingua_del_percorso("text/ui_fr.txt") == "fr")

    # ricerca dei file e font
    trovati = adapter_gamemaker.file_localizzazione(gioco)
    sorgenti, lingue, _ = adapter_gamemaker.scegli_sorgenti(trovati)
    controlla("file della lingua originale trovati, il francese no", sorgenti == [
        "Localization/english_npc.txt", "Localization/english_ui.txt"] and lingue == {"en", "fr"})
    ammessi = adapter_gamemaker.caratteri_font(gioco)
    controlla("font: caratteri comuni ai font di testo (il font di sole cifre non conta)",
              ammessi is not None and "à" in ammessi and "ù" not in ammessi and "a" in ammessi)
    controlla("italiano: ì->í, ò->ó, ù->ú se il font non li ha; virgolette semplici",
              caratteri.adatta_caratteri("Così può più \u201cciao\u201d", ammessi, "it") == 'Cosí puó piú "ciao"')
    controlla("gamemaker.font: nomi letti", [f["nome"] for f in gamemaker.font(open(os.path.join(gioco, "data.win"), "rb").read())[0]]
              == ["fn_testo", "fn_altro", "fn_numeri"])
    controlla("italiano: à è restano se il font li ha; l'acuto solo per i o u",
              caratteri.adatta_caratteri("è già", set(caratteri.ASCII) | set("áéíóú"), "it") == "e' gia'")
    # politica dei font: un font solo ASCII conta; uno coreano senza accenti no; uno di sole maiuscole vale per le minuscole
    prove = os.path.join(base, "font")
    os.makedirs(prove)
    coreano = set(chr(c) for c in range(32, 127)) | {chr(0xAC00 + i) for i in range(300)}
    maiuscole = set(chr(c) for c in range(32, 97)) | set("ÀÈÉÙÍÓÚ")
    casi = [("solo ASCII + accentato", [("a", latino), ("b", set(chr(c) for c in range(32, 127)))],
             lambda a: "a" in a and "è" not in a),
            ("coreano escluso", [("a", latino), ("k", coreano)], lambda a: "è" in a),
            ("sole maiuscole", [("a", latino), ("m", maiuscole)], lambda a: "a" in a and "è" in a)]
    for nome, fonts, atteso in casi:
        open(os.path.join(prove, "data.win"), "wb").write(data_win(fonts))
        controlla("font: " + nome, atteso(adapter_gamemaker.caratteri_font(prove) or set()))
    open(os.path.join(prove, "data.win"), "wb").write(b"FORM\x10\x00\x00\x00FONT")
    controlla("data.win troncato: solo ASCII, nessun errore", adapter_gamemaker.caratteri_font(prove) == set(caratteri.ASCII))

    # flusso completo con il traduttore di prova; profili e memorie nella cartella del test
    os.makedirs(os.path.join(base, "patchsmith", "profiles"))
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    automatico.CARTELLA_PROFILI = os.path.join(base, "patchsmith", "profiles")
    automatico.adapter_gamemaker  # noqa: B018 (importato)
    percorso, completato, _ = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)
    controlla("analizza e traduci: completato, profilo GameMaker", completato and percorso
              and '"motore": "gamemaker"' in open(percorso, encoding="utf-8").read())
    scritto = open(os.path.join(gioco, "Localization", "english_ui.txt"), "rb").read().decode()
    controlla("file tradotto: struttura intatta, spazio iniziale e codici conservati",
              "0 [it] Fight\r\n" in scritto and "5  [it] do next?\r\n" in scritto and "/name/" in scritto
              and "/n/" in scritto and scritto.startswith("// commento") and "31 100\r\n" in scritto)
    controlla("il file francese non e' toccato",
              impronte(gioco)[os.path.join("Localization", "french", "ui.txt")] == prima[os.path.join("Localization", "french", "ui.txt")])
    controlla("riapplica senza rete: ok", automatico.riapplica(gioco, silenzio, prova=True))
    automatico.ripristina(gioco, silenzio)
    controlla("ripristino: cartella del gioco identica a prima", impronte(gioco) == prima)
    progetto.gioco_aperto = lambda cartella: True
    controlla("gioco aperto: non scrivo nulla", not automatico.riapplica(gioco, silenzio, prova=True)
              and impronte(gioco) == prima)
    progetto.gioco_aperto = lambda cartella: False

    # file tradotto modificato a mano, poi riapplica e ripristina: l'originale inglese non si perde mai
    automatico.riapplica(gioco, silenzio, prova=True)
    ui_path = os.path.join(gioco, "Localization", "english_ui.txt")
    open(ui_path, "ab").write(b"99 Aggiunta a mano\r\n")
    righe_log = []
    automatico.riapplica(gioco, righe_log.append, prova=True)
    controlla("modifica a mano riconosciuta (non scambiata per un aggiornamento del gioco)",
              any("modificato a mano" in r for r in righe_log)
              and not any(f.endswith(".patchsmith-originale.1") for f in os.listdir(os.path.join(gioco, "Localization"))))
    automatico.ripristina(gioco, silenzio)
    controlla("modifica a mano + ripristino: l'originale inglese torna al suo posto, identico", impronte(gioco) == prima)

    # file cambiato dopo la traduzione e mai riapplicato: il ripristino non cancella l'originale
    automatico.riapplica(gioco, silenzio, prova=True)
    open(ui_path, "ab").write(b"98 Cambiato da altri\r\n")
    automatico.ripristina(gioco, silenzio)
    controlla("file cambiato da altri: backup dell'originale conservato",
              open(ui_path + ".patchsmith-originale", "rb").read() == UI.encode())
    os.remove(ui_path + ".patchsmith-originale")
    os.remove(ui_path + progetto.SUFFISSO_INFO)
    open(ui_path, "wb").write(UI.encode())
    controlla("... e rimesso a posto a mano la cartella torna identica", impronte(gioco) == prima)

    # il gioco ha gia' l'italiano: niente traduzione
    os.makedirs(os.path.join(gioco, "Localization", "italian"))
    open(os.path.join(gioco, "Localization", "italian", "ui.txt"), "wb").write(b"0 Combatti\r\n1 Cambia\r\n2 Cattura\r\n3 Fuggi\r\n4 Cosa deve fare \r\n5  ora?\r\n")
    percorso, completato, _ = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)
    controlla("lingua gia' presente nel gioco: niente traduzione", not completato
              and not os.path.exists(os.path.join(gioco, "Localization", "english_ui.txt.patchsmith-originale")))

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
