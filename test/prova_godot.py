"""Giochi Godot 4 con le tabelle delle traduzioni di Godot (adapter_godot).

Uso: python -I test/prova_godot.py <cartella_di_prova_che_non_esiste>
- lettura: PCK incorporato nell'eseguibile (formato 2, posizioni relative), risorse binarie RSRC, OptimizedTranslation
  (testi interi e compressi con smaz), Translation con il dizionario dei messaggi;
- flusso: Analizza e traduci con il traduttore finto -> override.cfg con l'autoload, script e traduzioni accanto
  all'eseguibile; Riapplica; Ripristina con la cartella identica (anche con un override.cfg del gioco);
- tabella in italiano gia' presente: lingua ufficiale, nessun file scritto.
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_godot, automatico, progetto  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def _s(testo):
    b = testo.encode("utf-8") + b"\0"
    return struct.pack("<I", len(b)) + b


def _variante(v):
    if isinstance(v, str):
        return struct.pack("<I", 5) + _s(v)
    if isinstance(v, dict):
        return struct.pack("<II", 26, len(v)) + b"".join(_variante(k) + _variante(x) for k, x in v.items())
    if isinstance(v, bytes):
        return struct.pack("<II", 31, len(v)) + v + b"\0" * ((4 - len(v) % 4) % 4)
    if isinstance(v, list):
        return struct.pack("<II", 32, len(v)) + struct.pack("<%di" % len(v), *v)
    raise TypeError(v)


def risorsa(tipo, proprieta):
    """File .translation binario come lo scrive Godot 4 (formato 6, una risorsa interna)."""
    nomi = list(proprieta)
    testa = b"RSRC" + struct.pack("<5I", 0, 0, 4, 4, 6) + _s(tipo) + struct.pack("<QIQ", 0, 2, 12345) + b"\0" * 44
    testa += struct.pack("<I", len(nomi)) + b"".join(_s(n) for n in nomi) + struct.pack("<I", 0)
    interna = _s("local://%s_1" % tipo)
    corpo = _s(tipo) + struct.pack("<I", len(nomi)) + b"".join(
        struct.pack("<I", i) + _variante(proprieta[n]) for i, n in enumerate(nomi))
    posizione = len(testa) + 4 + len(interna) + 8
    return testa + struct.pack("<I", 1) + interna + struct.pack("<Q", posizione) + corpo


def ottimizzata(testi, locale=None):
    """OptimizedTranslation minima: un secchio con tutti i testi; il primo compresso con smaz ("the" = codice 1)."""
    stringhe, elementi = b"", []
    for i, t in enumerate(testi):
        intero = t.encode("utf-8") + b"\0"
        dati = (b"\x01" + b"\xff" + bytes([len(t[3:].encode()) - 1]) + t[3:].encode()) \
            if i == 0 and t.startswith("the") else intero
        elementi += [i + 1000, len(stringhe), len(dati), len(intero)]
        stringhe += dati
    proprieta = {"hash_table": [0], "bucket_table": [len(testi), 0] + elementi, "strings": stringhe}
    if locale:
        proprieta["locale"] = locale
    return risorsa("OptimizedTranslation", proprieta)


def eseguibile_con_pck(file):
    """Un .exe finto con il PCK incorporato in fondo (come l'esportazione di Godot con "embed pck")."""
    voci, dati_file = b"", b""
    for nome, dati in file.items():
        n = nome.encode() + b"\0" * (4 - len(nome) % 4)
        voci += struct.pack("<I", len(n)) + n + struct.pack("<QQ", len(dati_file), len(dati)) + b"\0" * 16 + \
            struct.pack("<I", 0)
        dati_file += dati
    testa_lunghezza = 4 + 16 + 12 + 64 + 4
    base = testa_lunghezza + len(voci)
    pck = b"GDPC" + struct.pack("<4I", 2, 4, 4, 1) + struct.pack("<IQ", 2, base) + b"\0" * 64 + \
        struct.pack("<I", len(file)) + voci + dati_file
    return b"MZ" + b"\0" * 998 + pck + struct.pack("<Q", len(pck)) + b"GDPC"


def impronte(cartella):
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for n in files:
            with open(os.path.join(radice, n), "rb") as f:
                uscita[os.path.relpath(os.path.join(radice, n), cartella)] = hashlib.sha256(f.read()).hexdigest()
    return uscita


def gioco(cartella, con_italiano=False, override=None):
    file = {"project.binary": b"ECFG",
            "assets/translations/translations.en.translation": ottimizzata(
                ["the drill is ready", "Upgrade", "Unlock [color=40ff40]overdrive[/color] mode"]),
            "assets/translations/translations.de.translation": risorsa("Translation", {
                "locale": "de", "messages": {"UPGRADE": "Verbessern", "READY": "Der Bohrer ist bereit"}})}
    if con_italiano:
        file["assets/translations/translations.it.translation"] = ottimizzata(["Pronto"], locale="it")
    os.makedirs(cartella)
    with open(os.path.join(cartella, "drill.exe"), "wb") as f:
        f.write(eseguibile_con_pck(file))
    if override is not None:
        with open(os.path.join(cartella, "override.cfg"), "w", encoding="utf-8") as f:
            f.write(override)


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    automatico.CARTELLA_PROFILI = os.path.join(progetto.RADICE_DATI, "profiles")

    # lettura
    cartella = os.path.join(base, "Drill")
    gioco(cartella)
    pck = adapter_godot.Pck(os.path.join(cartella, "drill.exe"))
    tabelle = adapter_godot.tabelle(pck)
    en = adapter_godot.testi_tabella(pck.leggi(tabelle["en"]))
    de = adapter_godot.testi_tabella(pck.leggi(tabelle["de"]))
    controlla("PCK incorporato, tabelle per lingua; OptimizedTranslation (anche smaz) e Translation lette (%s, %s)"
              % (ascii(en), ascii(de)), pck.versione() == "4.4.1" and sorted(tabelle) == ["de", "en"]
              and en == ("en", ["the drill is ready", "Upgrade", "Unlock [color=40ff40]overdrive[/color] mode"])
              and de[0] == "de" and sorted(de[1]) == ["Der Bohrer ist bereit", "Verbessern"])

    # flusso completo con il traduttore finto
    prima = impronte(cartella)
    righe = []
    percorso, completato, esito = automatico.analizza_e_traduci(cartella, "auto", "it", "finto", righe.append)
    profilo = json.load(open(percorso, encoding="utf-8")) if percorso else {}
    with open(os.path.join(cartella, "override.cfg"), encoding="utf-8") as f:
        override = f.read()
    with open(os.path.join(cartella, adapter_godot.DIZIONARIO), encoding="utf-8") as f:
        dizionario = json.load(f)
    controlla("Analizza e traduci: profilo godot, override.cfg con l'autoload, script e traduzioni accanto all'eseguibile",
              completato and profilo.get("motore") == "godot" and esito["esito"].codice == "OK"
              and '[autoload]' in override and 'PatchsmithTraduzione="*res://patchsmith-traduzione.gd"' in override
              and os.path.exists(os.path.join(cartella, adapter_godot.SCRIPT))
              and dizionario["lingua"] == "en" and dizionario["aggiunte"] == ["it"]
              and dizionario["testi"].get("Upgrade", "").startswith("[it]")
              and "[color=40ff40]" in dizionario["testi"].get("Unlock [color=40ff40]overdrive[/color] mode", ""))
    controlla("il pacchetto del gioco non e' cambiato", impronte(cartella)["drill.exe"] == prima["drill.exe"])
    automatico.riapplica(cartella, righe.append, prova=True, esito={})
    automatico.ripristina(cartella, righe.append, esito={})
    controlla("Riapplica e poi Ripristina originali: cartella identica a prima", impronte(cartella) == prima)

    # override.cfg del gioco: conservato e rimesso
    altra = os.path.join(base, "DrillConOverride")
    gioco(altra, override="[display]\nwindow/size/mode=3\n")
    prima = impronte(altra)
    automatico.analizza_e_traduci(altra, "auto", "it", "finto", righe.append)
    with open(os.path.join(altra, "override.cfg"), encoding="utf-8") as f:
        unito = f.read()
    automatico.ripristina(altra, righe.append, esito={})
    controlla("override.cfg del gioco: le sue impostazioni restano, poi rimesso identico",
              "window/size/mode=3" in unito and "PatchsmithTraduzione" in unito and impronte(altra) == prima)

    # italiano gia' nel gioco
    ita = os.path.join(base, "DrillItaliano")
    gioco(ita, con_italiano=True)
    prima = impronte(ita)
    percorso, completato, esito = automatico.analizza_e_traduci(ita, "auto", "it", "finto", righe.append)
    controlla("tabella in italiano gia' nel gioco: lingua ufficiale, nessun file scritto",
              not completato and esito["esito"].codice == "UFFICIALE" and impronte(ita) == prima)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return 1 if falliti else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
