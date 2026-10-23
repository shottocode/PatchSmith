"""Test dell'adapter Unreal con un gioco finto (pak v9 costruito qui, in modo indipendente dall'adapter).

Uso: python -I test/prova_unreal.py <cartella_temporanea_vuota>
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_unreal  # noqa: E402

ESITI = []
MAGIC_LOCRES = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def fs(s):  # FString, scritto qui in modo indipendente
    if not s:
        return struct.pack("<i", 0)
    try:
        b = s.encode("ascii")
        return struct.pack("<i", len(b) + 1) + b + b"\0"
    except UnicodeEncodeError:
        b = s.encode("utf-16-le")
        return struct.pack("<i", -(len(b) // 2 + 1)) + b + b"\0\0"


def locres_v2(voci):
    """voci: [(namespace, chiave, testo)] -> .locres versione 2 con stringhe deduplicate."""
    stringhe, indice = [], {}
    per_ns = {}
    for ns, k, t in voci:
        if t not in indice:
            indice[t] = len(stringhe)
            stringhe.append([t, 0])
        stringhe[indice[t]][1] += 1
        per_ns.setdefault(ns, []).append((k, t))
    corpo = struct.pack("<I", len(voci)) + struct.pack("<I", len(per_ns))
    for ns, chiavi in per_ns.items():
        corpo += struct.pack("<I", 0x1234) + fs(ns) + struct.pack("<I", len(chiavi))
        for k, t in chiavi:
            corpo += struct.pack("<I", 0x5678) + fs(k) + struct.pack("<Ii", 0x9ABC, indice[t])
    offset = 16 + 1 + 8 + len(corpo)
    testa = MAGIC_LOCRES + b"\x02" + struct.pack("<q", offset) + corpo
    coda = struct.pack("<i", len(stringhe)) + b"".join(fs(t) + struct.pack("<i", rc) for t, rc in stringhe)
    return testa + coda


def locmeta(cultura):
    return bytes.fromhex("4fee4ca1684855836c4c46bd70da507c") + b"\x00" + fs(cultura) + fs(cultura + "/Game.locres")


def pak_v9(file):
    """Pak v9 non compresso, costruito indipendentemente dall'adapter."""
    corpo, voci = b"", []
    for nome, dati in file:
        h = hashlib.sha1(dati).digest()
        voce = lambda off, d=dati, h=h: struct.pack("<qqqI", off, len(d), len(d), 0) + h + b"\x00" + struct.pack("<I", 0)
        voci.append((nome, len(corpo), voce))
        corpo += voce(0) + dati
    indice = fs("../../../") + struct.pack("<i", len(voci)) + b"".join(fs(n) + v(o) for n, o, v in voci)
    coda = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 9, len(corpo), len(indice))
            + hashlib.sha1(indice).digest() + b"\x00" + b"\x00" * 160)
    return corpo + indice + coda


def crea_gioco(base):
    paks = os.path.join(base, "Proj", "Content", "Paks")
    os.makedirs(paks)
    os.makedirs(os.path.join(base, "Engine", "Binaries"))
    voci_en = [("UI", "Start", "Start Game"), ("UI", "Quit", "Quit"), ("ST_Help", "Tip1", "Press {key} to <Bold>jump</>"),
               ("ST_Help", "Tip2", "Can be interrupted;-;Knocks back enemies"), ("", "Num", "100"),
               ("UI", "Start2", "Start Game")]
    file = [("Proj/Content/Localization/Game/Game.locmeta", locmeta("en")),
            ("Proj/Content/Localization/Game/en/Game.locres", locres_v2(voci_en)),
            ("Proj/Content/Localization/Game/de/Game.locres", locres_v2([("UI", "Start", "Spiel starten")])),
            ("Proj/Content/Maps/Level.umap", b"mappa" * 100)]
    open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb").write(pak_v9(file))
    return paks


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    adapter_unreal.gioco_aperto = lambda c: False
    gioco = os.path.join(base, "gioco")
    paks = crea_gioco(gioco)
    prima = sorted(os.listdir(paks)) + sorted(os.listdir(gioco))

    info = adapter_unreal.analizza(gioco)
    controlla("analisi: un pak v9, nessun problema", info["versioni"] == [9] and not info["problemi"])
    sorgenti = adapter_unreal.trova_locres(gioco)
    controlla("trova solo il .locres della lingua originale (en)", [s[1] for s in sorgenti]
              == ["Proj/Content/Localization/Game/en/Game.locres"])
    _, testi, cultura = adapter_unreal.testi_da_tradurre(gioco, silenzio)
    controlla("testi da tradurre e lingua dal .locmeta", cultura == "en" and "Start Game" in testi and "100" not in testi)

    originale = sorgenti[0][0].leggi(sorgenti[0][1])
    info_locres = adapter_unreal.leggi_locres(originale)
    controlla("round-trip .locres identico", adapter_unreal.scrivi_locres(info_locres, [s for s, _ in info_locres["stringhe"]]) == originale)

    memoria = {"Start Game": "Inizia partita", "Quit": "Esci", "Press {key} to <Bold>jump</>": "Premi {key} per <Bold>saltare</>",
               "Can be interrupted;-;Knocks back enemies": "Può essere interrotto;-;Respinge i nemici"}
    n = adapter_unreal.installa(gioco, "it", "prova", memoria, silenzio)
    nuovo_pak = os.path.join(paks, adapter_unreal.nome_pak("it"))
    controlla("pak di patch scritto (4 frasi tradotte)", n == 4 and os.path.exists(nuovo_pak))
    letto = adapter_unreal.Pak(nuovo_pak)
    tradotto = adapter_unreal.leggi_locres(letto.leggi("Proj/Content/Localization/Game/en/Game.locres"))
    testi_trad = [s for s, _ in tradotto["stringhe"]]
    controlla("locres tradotto: chiavi e intestazione identiche", tradotto["chiavi"] == info_locres["chiavi"]
              and tradotto["testa"] == info_locres["testa"])
    controlla("locres tradotto: testi, segnaposto e accenti", "Inizia partita" in testi_trad and "100" in testi_trad
              and "Premi {key} per <Bold>saltare</>" in testi_trad and "Può essere interrotto;-;Respinge i nemici" in testi_trad)
    controlla("pak originale non toccato", sorted(os.listdir(paks)) == sorted(prima[:1] + [adapter_unreal.nome_pak("it")]))

    righe = []
    adapter_unreal.stato(gioco, righe.append)
    controlla("stato: pak presente e integro", any(r.strip().startswith("OK") for r in righe))
    controlla("punteggiatura tipografica nuova -> ASCII, caratteri che troncano -> scartata",
              adapter_unreal._adatta("L’arma…", set()) == "L'arma..."
              and adapter_unreal._adatta("L’arma", {"’"}) == "L’arma"
              and adapter_unreal._adatta("a\x00b", set()) is None and adapter_unreal._adatta("a￿b", set()) is None)

    # cambio lingua: il pak vecchio viene sostituito
    adapter_unreal.installa(gioco, "es", "prova", {"Start Game": "Iniciar"}, silenzio)
    controlla("cambio lingua: pak it tolto, pak es scritto", os.path.exists(os.path.join(paks, adapter_unreal.nome_pak("es")))
              and not os.path.exists(nuovo_pak))

    # disinstallazione: tutto come prima
    adapter_unreal.disinstalla(gioco, silenzio)
    controlla("disinstalla: cartelle identiche a prima", sorted(os.listdir(paks)) + sorted(os.listdir(gioco)) == prima)

    # pak con lo stesso nome non creato da noi: non si sovrascrive
    open(nuovo_pak, "wb").write(b"di qualcun altro")
    try:
        adapter_unreal.installa(gioco, "it", "prova", memoria, silenzio)
        controlla("pak altrui: non sovrascritto", False)
    except adapter_unreal.ErroreUnreal:
        controlla("pak altrui: non sovrascritto", open(nuovo_pak, "rb").read() == b"di qualcun altro")
    os.remove(nuovo_pak)

    # pak modificato da altri dopo l'installazione: lasciato
    adapter_unreal.installa(gioco, "it", "prova", memoria, silenzio)
    from patchsmith import __version__
    controlla("manifesto con la versione di PatchSmith (per il controllo dopo aver giocato)",
              adapter_unreal.leggi_manifesto(gioco).get("patchsmith_versione") == __version__)
    open(nuovo_pak, "ab").write(b"x")
    adapter_unreal.disinstalla(gioco, silenzio)
    controlla("pak modificato da altri: lasciato", os.path.exists(nuovo_pak))
    os.remove(nuovo_pak)

    # manifesto alterato: nulla cancellato
    falso = {"patchsmith": True, "file": {"Proj/Content/Paks/Proj-WindowsNoEditor.pak": "0" * 64}}
    open(os.path.join(gioco, adapter_unreal.MANIFESTO), "w").write(json.dumps(falso))
    try:
        adapter_unreal.disinstalla(gioco, silenzio)
        controlla("manifesto alterato: rifiutato", False)
    except adapter_unreal.ErroreUnreal:
        controlla("manifesto alterato: pak originale intatto", os.path.exists(os.path.join(paks, "Proj-WindowsNoEditor.pak")))
    os.remove(os.path.join(gioco, adapter_unreal.MANIFESTO))

    # installazione interrotta dopo il manifesto e prima di mettere il pak al suo posto: si ripristina tutto
    adapter_unreal.installa(gioco, "it", "prova", memoria, silenzio)
    vero_replace = os.replace

    def replace_interrotto(a, b):
        if b.endswith(".pak"):
            raise KeyboardInterrupt
        return vero_replace(a, b)

    adapter_unreal.os.replace = replace_interrotto
    try:
        adapter_unreal.installa(gioco, "it", "prova", dict(memoria, Quit="Abbandona"), silenzio)
    except KeyboardInterrupt:
        pass
    finally:
        adapter_unreal.os.replace = vero_replace
    adapter_unreal.disinstalla(gioco, silenzio)
    controlla("installazione interrotta: il ripristino toglie tutto", sorted(os.listdir(paks)) + sorted(os.listdir(gioco)) == prima)

    # pak firmati (.sig): rifiutato
    sig = os.path.join(paks, "Proj-WindowsNoEditor.sig")
    open(sig, "wb").write(b"firma")
    try:
        adapter_unreal.installa(gioco, "it", "prova", memoria, silenzio)
        controlla("pak firmati: rifiutato", False)
    except adapter_unreal.ErroreUnreal:
        controlla("pak firmati: rifiutato", not os.path.exists(nuovo_pak))
    os.remove(sig)

    # aggiornamento del gioco: lo stato lo segnala
    adapter_unreal.installa(gioco, "it", "prova", memoria, silenzio)
    open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb").write(pak_v9([
        ("Proj/Content/Localization/Game/Game.locmeta", locmeta("en")),
        ("Proj/Content/Localization/Game/en/Game.locres", locres_v2([("UI", "Start", "Start Game"), ("UI", "New", "New Quest")]))]))
    righe = []
    adapter_unreal.stato(gioco, righe.append)
    controlla("gioco aggiornato: segnalato", any("aggiornato" in r for r in righe))
    adapter_unreal.disinstalla(gioco, silenzio)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
