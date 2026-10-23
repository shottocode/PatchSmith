"""Test dell'adapter Unity Localization (tabelle dei testi dentro gli AssetBundle).

Uso: python -I test/prova_unity_loc.py <cartella_temporanea_vuota> [<bundle con le tabelle dei testi>]
Sempre, con bundle e giochi finti (test/unity_finto.py, nessun file di giochi veri):
- lettura dei bundle (senza compressione, LZ4, LZMA, anche a pezzi) e delle tabelle dei testi; le tabelle degli asset
  (riferimenti, non testi) non si traducono;
- la lingua di un bundle dal nome ("english(en)"), dal contenuto ("english", "localization_en", "chinese(simplified)")
  o dal nome della lingua se il bundle non si legge; la lingua ufficiale trovata cosi' (lingua_ufficiale);
- gioco IL2CPP con il catalogo che controlla l'impronta (CRC): si toglie solo quella del bundle tradotto, con backup;
  "Ripristina originali" rimette catalogo e bundle identici; il controllo dopo aver giocato vede il catalogo rimesso
  dal gioco;
- catalogo binario (catalog.bin), CRC che non si trova, anti-cheat, tabelle senza la lingua di partenza, italiano
  gia' nel gioco: nessun file modificato e un motivo chiaro.
Con un bundle vero (es. quello inglese di un gioco, che viene solo letto e copiato): riscrittura identica, testi
cambiati e riletti, flusso completo con il traduttore di prova su una copia minima del gioco e ripristino identico.
"""
import hashlib
import json
import os
import shutil
import sys

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

from patchsmith import adapter_unity_loc, automatico, controllo, lingua_ufficiale, progetto, rilevamento  # noqa: E402
from patchsmith import unityfs  # noqa: E402
import unity_finto as finto  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def impronta(percorso):
    with open(percorso, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def leggi(percorso):
    with open(percorso, "rb") as f:
        return f.read()


EN = ["New Game", "Continue", "Options", "The door is locked. Find the key first.", "Quit to desktop"]
EN2 = ["Talk to the blacksmith", "You found a rusty sword!"]
FR = ["Nouvelle partie", "Continuer", "Options", "La porte est fermée.", "Quitter"]
IT = ["Nuova partita", "Continua", "Opzioni", "La porta è chiusa.", "Esci"]
GUID = ["0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"]


def tabella(nome, codice, testi, **opzioni):
    return finto.bundle_tabelle([(nome, codice, testi)], **opzioni)


def prova_formato(base):
    controlla("LZ4: letterali e ripetizioni (anche sovrapposte)",
              unityfs.lz4_decomprimi(b"\x50hello", 5) == b"hello"
              and unityfs.lz4_decomprimi(b"\x32abc\x03\x00", 9) == b"abcabcabc"
              and unityfs.lz4_decomprimi(b"\x15a\x01\x00", 10) == b"a" * 10)
    interno = finto.file_tabelle([("UI_en", "en", EN), ("Asset_en", "en", GUID)])
    risorse = b"\x05\x00\x00\x00Ciao!" * 300
    tutti = True
    for compressione in (None, "lz4", "lzma"):
        byte = finto.bundle([("CAB-1", interno), ("CAB-1.resS", risorse)], compressione=compressione, blocco=700)
        b = unityfs.Bundle(byte)
        testi = [t for _, t in adapter_unity_loc.FileTabelle(byte).testi()]
        percorso = os.path.join(base, "formato-%s.bundle" % compressione)
        with open(percorso, "wb") as f:
            f.write(byte)
        vecchio = unityfs._PEZZO_LZMA
        unityfs._PEZZO_LZMA = 500  # anche l'LZMA a pezzi (nei giochi: blocchi da piu' di 16 MB)
        try:
            with unityfs.Indice(percorso) as indice:
                pezzi = {n: b"".join(p for _, p in indice.pezzi(s, s + d)) for n, s, d, _ in indice.nodi}
        finally:
            unityfs._PEZZO_LZMA = vecchio
        tutti = tutti and b.file["CAB-1"] == interno and testi == EN and pezzi == {"CAB-1": interno,
                                                                                  "CAB-1.resS": risorse}
    controlla("bundle senza compressione, LZ4 e LZMA (a blocchi): file interni e tabelle letti, anche a pezzi; la "
              "tabella degli asset (riferimenti) non e' tra i testi", tutti)
    rotto = os.path.join(base, "rotto.bundle")
    with open(rotto, "wb") as f:
        f.write(finto.bundle([("CAB-1", interno)], compressione="lz4", blocco=700)[:-50])  # troncato
    try:
        with unityfs.Indice(rotto) as indice:
            for _ in indice.pezzi():
                pass
        errore = None
    except ValueError as e:
        errore = e
    controlla("bundle troncato: errore di formato (ValueError), non un crash", isinstance(errore, ValueError))
    # senza compressione e in un solo blocco (come i bundle grandi di SWORN o LumenTale): letto a pezzi, solo la parte
    # che serve
    grande = os.path.join(base, "un-blocco.bundle")
    with open(grande, "wb") as f:
        f.write(finto.bundle([("CAB-1", interno), ("CAB-1.resS", risorse)], blocco=1 << 30))
    vecchio = unityfs._PEZZO_LZMA
    unityfs._PEZZO_LZMA = 500
    try:
        with unityfs.Indice(grande) as indice:
            blocchi = len(indice.blocchi)
            _, s, d, _ = indice.nodi[1]
            pezzi = list(indice.pezzi(s + 10, s + 1700))
    finally:
        unityfs._PEZZO_LZMA = vecchio
    controlla("bundle senza compressione in un solo blocco: letto a pezzi piccoli, solo la parte chiesta",
              blocchi == 1 and len(pezzi) == 4 and all(len(p) <= 500 for _, p in pezzi)
              and pezzi[0][0] == s + 10 and b"".join(p for _, p in pezzi) == risorse[10:1700])
    # LZMA a pezzi con il flusso che finisce prima della dimensione scritta nell'indice: niente EOFError
    corto = bytearray(finto.bundle([("CAB-1", interno)], compressione="lzma", blocco=1 << 20))
    q = corto.index(b"\x00" * 16 + b"\x00\x00\x00\x01") + 20  # prima voce dei blocchi: dimensione decompressa
    corto[q:q + 4] = (int.from_bytes(corto[q:q + 4], "big") + 300).to_bytes(4, "big")
    percorso = os.path.join(base, "lzma-corto.bundle")
    with open(percorso, "wb") as f:
        f.write(bytes(corto))
    unityfs._PEZZO_LZMA = 500
    try:
        with unityfs.Indice(percorso) as indice:
            letto = b"".join(p for _, p in indice.pezzi())
        errore = None
    except Exception as e:  # noqa: BLE001 - qualunque errore che non sia di formato e' il difetto cercato
        letto, errore = b"", e
    finally:
        unityfs._PEZZO_LZMA = vecchio
    controlla("LZMA a pezzi piu' corto del previsto: letto quello che c'e', nessun EOFError",
              (errore is None and letto == interno) or isinstance(errore, ValueError))
    f = adapter_unity_loc.FileTabelle(finto.bundle_tabelle([("UI_en", "en", EN)]))
    riletti = [t for _, t in adapter_unity_loc.FileTabelle(f.serializza(
        {pos: "IT %d àèìòù" % k for k, (pos, _) in enumerate(f.testi())})).testi()]
    controlla("tabella riscritta (lunghezze diverse, accenti): riletta uguale e nello stesso ordine",
              riletti == ["IT %d àèìòù" % k for k in range(len(EN))])


def prova_lingue(base):
    # nomi come LumenTale: la lingua per esteso, senza codice; i Locales e i dati condivisi non sono lingue
    a = os.path.join(base, "nomi-lumentale")
    os.makedirs(a)
    for nome, byte in {
        "localization-string-tables-english_assets_all.bundle": tabella("UI_en", "en", EN, compressione="lz4"),
        "localization-string-tables-italiano_assets_all.bundle": tabella("UI_it", "it", IT),
        "localization-string-tables-français_assets_all.bundle": tabella("UI_fr", "fr", FR),
        "localization-locales_assets_all.bundle": finto.bundle([("CAB-loc", b"\x00" * 64)]),
        "localization-assets-shared_assets_all.bundle": tabella("Immagini", "en", GUID),
    }.items():
        with open(os.path.join(a, nome), "wb") as f:
            f.write(byte)
    controlla("lingua dal contenuto (m_LocaleId): 'english', 'italiano', 'français' senza codice nel nome; Locales e "
              "tabelle degli asset ignorati",
              adapter_unity_loc.tabelle_per_contenuto(a) == {
                  "en": "localization-string-tables-english_assets_all.bundle",
                  "it": "localization-string-tables-italiano_assets_all.bundle",
                  "fr": "localization-string-tables-français_assets_all.bundle"})
    # nomi come Lego Party e Rogue Blight; un bundle illeggibile vale per il nome della lingua
    b = os.path.join(base, "nomi-vari")
    os.makedirs(b)
    for nome, byte in {
        "localization_en_assets_all.bundle": tabella("config_ui_en", "en", EN),
        "localization-string-tables-chinese(simplified)_assets_all.bundle": tabella("UI_zh", "zh-CN", ["开始游戏"]),
        "localization-string-tables-deutsch_assets_all.bundle": b"non e' un bundle",
        "localization-asset-tables-english(en)_assets_all.bundle": tabella("Asset_en", "en", EN),
        "localization-string-tables-polish(pl)_assets_all.bundle": b"rovinato, ma il codice e' nel nome",
    }.items():
        with open(os.path.join(b, nome), "wb") as f:
            f.write(byte)
    controlla("lingua: 'localization_en', 'chinese(simplified)' dal contenuto (zh-CN), '(pl)' dal nome, 'deutsch' "
              "illeggibile dal nome della lingua; tabelle degli asset escluse",
              sorted(adapter_unity_loc.tabelle_per_contenuto(b)) == ["de", "en", "pl", "zh-cn"]
              and adapter_unity_loc.tabelle(b)["en"] == "localization_en_assets_all.bundle")
    # lingua ufficiale: il catalogo non elenca i Locales, le tabelle dicono che c'e' l'italiano
    gioco = os.path.join(base, "ufficiale-dal-contenuto")
    finto.gioco_il2cpp(gioco, {"localization-string-tables-english_assets_all.bundle": tabella("UI_en", "en", EN),
                               "localization-string-tables-italiano_assets_all.bundle": tabella("UI_it", "it", IT)},
                       ("catalog.json", finto.catalogo([])))
    lingue, dati = lingua_ufficiale.lingue_nei_file(gioco, "Unity", "it")
    controlla("lingua ufficiale: italiano trovato dalle tabelle (il catalogo non lo dice)",
              dati.get("trovata") is True and {"en", "it"} <= {c.lower() for c in lingue})


def _flusso(base, nome, bundle_aa, catalogo, extra=None, origine="en"):
    """Analizza e traduci (traduttore di prova) su un gioco IL2CPP finto: (gioco, profilo, esito, impronte prima)."""
    gioco = os.path.join(base, nome)
    cartella_bundle = finto.gioco_il2cpp(gioco, bundle_aa, catalogo, extra)
    prima = {n: impronta(os.path.join(cartella_bundle, n)) for n in bundle_aa}
    if catalogo:
        prima[catalogo[0]] = impronta(os.path.join(os.path.dirname(cartella_bundle), catalogo[0]))
    profilo, _completo, esito = automatico.analizza_e_traduci(gioco, origine, "it", backend="finto", log=silenzio)
    return gioco, cartella_bundle, profilo, esito, prima


def _invariati(cartella_bundle, prima):
    aa = os.path.dirname(cartella_bundle)
    return all(impronta(os.path.join(cartella_bundle if n.endswith(".bundle") else aa, n)) == h
               for n, h in prima.items()) and not any("patchsmith" in n for d in (aa, cartella_bundle)
                                                      for n in os.listdir(d))


def prova_crc(base):
    en = "localization-string-tables-english(en)_assets_all.bundle"
    fr = "localization-string-tables-french(fr)_assets_all.bundle"
    catalogo = finto.catalogo([(en, 3530401984), (fr, 167159835)])
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "crc", {en: tabella("UI_en", "en", EN, compressione="lz4"), fr: tabella("UI_fr", "fr", FR)},
        ("catalog.json", catalogo))
    percorso_catalogo = os.path.join(os.path.dirname(cartella_bundle), "catalog.json")
    voci = json.load(open(profilo, encoding="utf-8"))["file"] if profilo else []
    controlla("CRC controllato: profilo con il catalogo da sistemare per il bundle inglese",
              voci == [{"percorso": "Gioco_Data/StreamingAssets/aa/StandaloneWindows64/" + en,
                        "formato": "unity-tabelle", "catalogo": "Gioco_Data/StreamingAssets/aa/catalog.json"}])
    tradotti = [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella_bundle, en)).testi()]
    dopo = leggi(percorso_catalogo)
    controlla("CRC: bundle inglese tradotto; nel catalogo CRC 0 solo per lui (il francese resta), stessa lunghezza",
              tradotti == ["[it] " + t for t in EN] and finto.crc_nel_catalogo(dopo) == {en: 0, fr: 167159835}
              and len(dopo) == len(catalogo) and impronta(os.path.join(cartella_bundle, fr)) == prima[fr])
    controlla("CRC: backup del catalogo originale e sue informazioni (come gli altri file)",
              leggi(percorso_catalogo + progetto.SUFFISSO_BACKUP) == catalogo
              and progetto.leggi_info(percorso_catalogo) is not None)
    esito_riapplica = {}
    controlla("CRC: Riapplica rifa' lo stesso (catalogo e bundle come prima)",
              automatico.riapplica(gioco, silenzio, prova=True, esito=esito_riapplica)
              and leggi(percorso_catalogo) == dopo and finto.crc_nel_catalogo(dopo)[en] == 0)
    # una nuova "Analizza e traduci": nel gioco il catalogo ha gia' il CRC 0 (scritto da PatchSmith), ma la voce del
    # profilo deve continuare a nominarlo (vale l'originale nel backup)
    profilo2, _completo, esito2 = automatico.analizza_e_traduci(gioco, "en", "it", backend="finto", log=silenzio)
    controlla("CRC: una nuova 'Analizza e traduci' tiene il catalogo nel profilo (conta l'originale, non il CRC gia' 0)",
              profilo2 == profilo and esito2["esito"].codice == "OK"
              and json.load(open(profilo, encoding="utf-8"))["file"] == voci and leggi(percorso_catalogo) == dopo)
    # il gioco rimette i suoi file (verifica dei file di Steam): il controllo dopo aver giocato lo vede
    shutil.copyfile(percorso_catalogo + progetto.SUFFISSO_BACKUP, percorso_catalogo)
    shutil.copyfile(os.path.join(cartella_bundle, en + progetto.SUFFISSO_BACKUP), os.path.join(cartella_bundle, en))
    c = controllo._Contesto(profilo, json.load(open(profilo, encoding="utf-8")), {}, silenzio, False, None)
    problemi = controllo._verifica_file(c)
    controlla("CRC: catalogo rimesso dal gioco -> il controllo dopo aver giocato lo rimette (Riapplica)",
              any(p.codice == "RIPRISTINATO_DAL_GIOCO" and "catalog.json" in p.messaggio for p in problemi))
    automatico.riapplica(gioco, silenzio, prova=True)
    controlla("CRC: dopo Riapplica bundle di nuovo tradotto e CRC di nuovo 0 nel catalogo",
              [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella_bundle, en)).testi()]
              == ["[it] " + t for t in EN] and leggi(percorso_catalogo) == dopo)
    controlla("CRC: Ripristina originali rimette catalogo e bundle identici, nessun file di PatchSmith",
              automatico.ripristina(gioco, silenzio) and _invariati(cartella_bundle, prima))

    # due bundle per la lingua di partenza (tabelle impacchettate separatamente), entrambi con il CRC
    en2 = "localization-string-tables-english(en)_assets_dialogues.bundle"
    catalogo = finto.catalogo([(en, 11), (en2, 22), (fr, 33)])
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "crc-due", {en: tabella("UI_en", "en", EN), en2: tabella("Dialoghi_en", "en", EN2, compressione="lzma"),
                          fr: tabella("UI_fr", "fr", FR)}, ("catalog.json", catalogo))
    dopo = leggi(os.path.join(os.path.dirname(cartella_bundle), "catalog.json"))
    controlla("CRC con due bundle inglesi: tradotti entrambi, CRC 0 per entrambi, il francese resta",
              [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella_bundle, en2)).testi()]
              == ["[it] " + t for t in EN2] and finto.crc_nel_catalogo(dopo) == {en: 0, en2: 0, fr: 33})
    controlla("CRC con due bundle: Ripristina rimette tutto identico",
              automatico.ripristina(gioco, silenzio) and _invariati(cartella_bundle, prima))

    # un aggiornamento del gioco cambia solo il catalogo (non la tabella inglese): il catalogo nuovo ha di nuovo il CRC
    # e il gioco non caricherebbe la tabella tradotta. Il controllo dopo aver giocato lo sistema da solo (Riapplica).
    catalogo = finto.catalogo([(en, 3530401984), (fr, 167159835)])
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "crc-aggiornato", {en: tabella("UI_en", "en", EN), fr: tabella("UI_fr", "fr", FR)},
        ("catalog.json", catalogo))
    percorso_catalogo = os.path.join(os.path.dirname(cartella_bundle), "catalog.json")
    aggiornato = finto.catalogo([(en, 3530401984), (fr, 4242), ("nuovo_assets_all.bundle", 77)])
    with open(percorso_catalogo, "wb") as f:
        f.write(aggiornato)
    c = controllo._Contesto(profilo, json.load(open(profilo, encoding="utf-8")), {}, silenzio, False, None)
    problemi = controllo._verifica_file(c)
    controlla("CRC: aggiornamento che cambia solo il catalogo -> correzione automatica (Riapplica), non 'Analizza e "
              "traduci'",
              [p.codice for p in problemi] == ["RIPRISTINATO_DAL_GIOCO"] and problemi[0].sicuro
              and problemi[0].chiave == "riapplica" and "catalog.json" in problemi[0].messaggio)
    automatico.riapplica(gioco, silenzio, prova=True)
    dopo = leggi(percorso_catalogo)
    controlla("CRC: dopo l'aggiornamento il catalogo nuovo ha il CRC 0 per il bundle tradotto (il resto come "
              "l'aggiornamento) e diventa l'originale",
              finto.crc_nel_catalogo(dopo) == {en: 0, fr: 4242, "nuovo_assets_all.bundle": 77}
              and leggi(percorso_catalogo + progetto.SUFFISSO_BACKUP) == aggiornato
              and [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella_bundle, en)).testi()]
              == ["[it] " + t for t in EN])
    controlla("CRC: dopo l'aggiornamento Ripristina rimette il catalogo aggiornato e il bundle originale",
              automatico.ripristina(gioco, silenzio) and leggi(percorso_catalogo) == aggiornato
              and impronta(os.path.join(cartella_bundle, en)) == prima[en]
              and not [n for d in (cartella_bundle, os.path.dirname(cartella_bundle)) for n in os.listdir(d)
                       if "patchsmith" in n])

    # catalogo senza CRC (come SWORN): il catalogo non si tocca
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "crc-zero", {en: tabella("UI_en", "en", EN)}, ("catalog.json", finto.catalogo([(en, 0)])))
    controlla("CRC 0 (come SWORN): bundle tradotto, catalogo intatto e senza backup",
              esito["esito"].codice == "OK" and impronta(os.path.join(os.path.dirname(cartella_bundle),
                                                                      "catalog.json")) == prima["catalog.json"]
              and not os.path.exists(os.path.join(os.path.dirname(cartella_bundle), "catalog.json"
                                                  + progetto.SUFFISSO_BACKUP))
              and "catalogo" not in json.load(open(profilo, encoding="utf-8"))["file"][0])


def prova_rifiuti(base):
    en = "localization-string-tables-english(en)_assets_all.bundle"
    # catalogo binario (Addressables 2, Unity 6): non si sa se controlla l'impronta
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "catalogo-bin", {en: tabella("UI_en", "en", EN)}, ("catalog.bin", b"\x01\x02catalogo binario"))
    e = esito["esito"]
    controlla("catalog.bin: non supportato con il motivo del catalogo binario, nessun file modificato",
              profilo is None and e.codice == "NON_SUPPORTATO" and "catalog.bin" in e.motivo
              and "BepInEx" not in e.motivo and _invariati(cartella_bundle, prima))
    # il catalogo controlla l'impronta di altri bundle e non nomina questo: non si sa togliere
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "crc-sconosciuto", {en: tabella("UI_en", "en", EN)},
        ("catalog.json", finto.catalogo([("altro.bundle", 777)])))
    e = esito["esito"]
    controlla("CRC che non si trova nel catalogo: non supportato (impronta), nessun file modificato",
              profilo is None and e.codice == "NON_SUPPORTATO" and "CRC" in e.motivo
              and _invariati(cartella_bundle, prima))
    # anti-cheat: prima di guardare le tabelle, anche nei giochi IL2CPP
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "anticheat", {en: tabella("UI_en", "en", EN)}, ("catalog.json", finto.catalogo([(en, 0)])),
        extra={"EasyAntiCheat/EasyAntiCheat_EOS_Setup.exe": b"finto"})
    controlla("anti-cheat (IL2CPP con tabelle): rilevato, ANTICHEAT, nessun bundle scritto",
              any(r["sezione"] == "AntiCheat" for r in rilevamento.rileva(gioco)["risultati"])
              and profilo is None and esito["esito"].codice == "ANTICHEAT" and _invariati(cartella_bundle, prima))
    # tabelle, ma non nella lingua di partenza: il motivo lo dice (non "serve BepInEx 6")
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "senza-inglese", {"localization-string-tables-french(fr)_assets_all.bundle": tabella("UI_fr", "fr", FR)},
        ("catalog.json", finto.catalogo([])))
    e = esito["esito"]
    controlla("tabelle senza la lingua di partenza: motivo chiaro con le lingue del gioco (per nome) e cosa scegliere, "
              "nessun file modificato",
              profilo is None and e.codice == "NON_SUPPORTATO" and "tabelle dei testi" in e.motivo
              and "lingua di partenza" in e.motivo and "Francese (fr)" in e.motivo and '"Da:"' in e.motivo
              and "IL2CPP" not in e.motivo and "BepInEx" not in e.motivo and _invariati(cartella_bundle, prima))
    # scelta la lingua che il gioco ha come lingua di partenza: la sua tabella si traduce
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "partenza-francese", {"localization-string-tables-french(fr)_assets_all.bundle": tabella("UI_fr", "fr", FR)},
        ("catalog.json", finto.catalogo([])), origine="fr")
    controlla("tabelle senza inglese, partenza scelta Francese: la tabella francese si traduce",
              esito["esito"].codice == "OK" and [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(
                  cartella_bundle, "localization-string-tables-french(fr)_assets_all.bundle")).testi()]
              == ["[it] " + t for t in FR])
    # italiano gia' nel gioco, con nomi alla Lego Party: lingua ufficiale
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "italiano-dentro", {"localization_en_assets_all.bundle": tabella("ui_en", "en", EN),
                                  "localization_it_assets_all.bundle": tabella("ui_it", "it", IT)},
        ("catalog.json", finto.catalogo([])))
    controlla("italiano gia' nel gioco (nomi 'localization_it'): UFFICIALE, nessun file modificato",
              profilo is None and esito["esito"].codice == "UFFICIALE" and _invariati(cartella_bundle, prima))
    # nomi senza codice: la tabella inglese si trova dal contenuto e si traduce (prima: "non supportato")
    gioco, cartella_bundle, profilo, esito, prima = _flusso(
        base, "nomi-senza-codice", {"localization-string-tables-english_assets_all.bundle":
                                    tabella("UI_en", "en", EN, compressione="lz4")},
        ("catalog.json", finto.catalogo([("localization-string-tables-english_assets_all.bundle", 0)])))
    tradotti = [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(
        cartella_bundle, "localization-string-tables-english_assets_all.bundle")).testi()]
    controlla("tabella inglese senza codice nel nome: trovata dal contenuto e tradotta",
              esito["esito"].codice == "OK" and tradotti == ["[it] " + t for t in EN])


def prova_bundle_vero(base, bundle):
    dati = open(bundle, "rb").read()
    f = adapter_unity_loc.FileTabelle(dati)
    testi = f.testi()
    b = unityfs.Bundle(dati)
    nome = b.nodi[0][0]
    s = unityfs.FileSerializzato(b.file[nome])
    controlla("bundle letto: %d testi; file interno riscritto senza cambi identico byte per byte" % len(testi),
              testi and s.con_testi_nuovi({}) == b.file[nome])
    nuovi = {pos: "IT %d àèìòù" % k for k, (pos, _) in enumerate(testi)}
    riletti = adapter_unity_loc.FileTabelle(f.serializza(nuovi)).testi()
    controlla("testi cambiati (lunghezze diverse, accenti): riletti uguali e nello stesso ordine",
              [t for _, t in riletti] == ["IT %d àèìòù" % k for k in range(len(testi))])
    # flusso completo su una copia minima del gioco: solo il bundle e il catalogo, sotto *_Data
    gioco = os.path.join(base, "Gioco")
    cartella_bundle = os.path.join(gioco, "Gioco_Data", "StreamingAssets", "aa", "StandaloneWindows64")
    os.makedirs(cartella_bundle)
    copia = os.path.join(cartella_bundle, os.path.basename(bundle))
    shutil.copyfile(bundle, copia)
    catalogo = os.path.join(os.path.dirname(os.path.dirname(bundle)), "catalog.json")
    if os.path.exists(catalogo):
        shutil.copyfile(catalogo, os.path.join(os.path.dirname(cartella_bundle), "catalog.json"))
    prima = hashlib.sha256(open(copia, "rb").read()).hexdigest()
    percorso = automatico._prepara_unity_tabelle(gioco, None, "en", "it", silenzio)
    p = progetto.Progetto(percorso, silenzio, prova=True)
    p.memoria = {t: "[it] " + t for _, t in testi}
    p._salva_memoria = lambda: None
    p.applica()
    tradotti = [t for _, t in adapter_unity_loc.FileTabelle(copia).testi()]
    controlla("flusso: tabella inglese tradotta nel bundle, con backup",
              tradotti == ["[it] " + t for _, t in testi] and os.path.exists(copia + progetto.SUFFISSO_BACKUP))
    controlla("ripristino: bundle identico all'originale, nessun file di PatchSmith rimasto",
              automatico.ripristina(gioco, silenzio) and hashlib.sha256(open(copia, "rb").read()).hexdigest()
              == prima and not [n for n in os.listdir(cartella_bundle) if "patchsmith" in n])


def _catalogo_bin(voci, doppio=None):
    """catalog.bin finto come quello di Addressables 2 (Big Dig): per ogni bundle il nome della voce con l'impronta,
    l'impronta Hash128 (16 byte), il nome del bundle (stringa con la lunghezza davanti) e il record di 5 numeri
    (impronta, nome, CRC, dimensione, dati comuni). doppio: nome di un bundle con un secondo record (ambiguo)."""
    import struct
    dati = bytearray(b"\x42\x89\xe3\x0d\x02\x00\x00\x00" + b"\x00" * 24)

    def stringa(s):
        dati.extend(struct.pack("<I", len(s)))
        inizio = len(dati)
        dati.extend(s)
        return inizio
    for i, (nome, crc) in enumerate(voci):
        esa = ("%02x" % (i + 1)) * 16
        stringa(("%s_%s.bundle" % (nome[:-len(".bundle")], esa)).encode())
        stringa(("{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\%s" % nome).encode())
        h = len(dati)
        dati.extend(bytes.fromhex(esa))
        b = stringa(esa.encode())
        for _ in range(2 if nome == doppio else 1):
            dati.extend(struct.pack("<5I", h, b, crc, 1234 + i, 0x20))
        dati.extend(b"\x00\x01")  # i record non sono allineati a 4 byte
    return bytes(dati)


def prova_catalogo_bin(base):
    """Catalogo binario (catalog.bin, Addressables 2): il CRC del bundle tradotto si azzera, nient'altro."""
    import struct
    inglese = "localization-string-tables-english(en)_assets_all.bundle"
    francese = "localization-string-tables-french(fr)_assets_all.bundle"
    aa = os.path.join(base, "aa")
    os.makedirs(os.path.join(aa, "StandaloneWindows64"))
    for n in (inglese, francese):
        open(os.path.join(aa, "StandaloneWindows64", n), "wb").write(b"UnityFS")
    catalogo = os.path.join(aa, "catalog.bin")
    byte = _catalogo_bin([(francese, 0x11111111), (inglese, 0xd345c8b3)])
    open(catalogo, "wb").write(byte)
    bundle = os.path.join(aa, "StandaloneWindows64", inglese)
    stato = adapter_unity_loc.crc_del_bundle(bundle)
    nuovo = adapter_unity_loc.togli_crc(byte, [inglese])
    diversi = [i for i in range(len(byte)) if byte[i] != nuovo[i]] if nuovo and len(nuovo) == len(byte) else None
    crc_fr = adapter_unity_loc._record_bin(byte, francese)
    controlla("catalog.bin: CRC del bundle inglese trovato ('controllato'); tolto solo quello (4 byte a 0, il francese "
              "resta)", stato[0] == "controllato" and diversi is not None and len(diversi) == 4
              and nuovo[diversi[0]:diversi[0] + 4] == b"\x00" * 4 and crc_fr
              and struct.unpack_from("<I", nuovo, crc_fr[0])[0] == 0x11111111)
    open(catalogo, "wb").write(nuovo)
    controlla("catalog.bin: dopo la modifica il bundle risulta 'libero'",
              adapter_unity_loc.crc_del_bundle(bundle)[0] == "libero")
    ambiguo = _catalogo_bin([(inglese, 0xd345c8b3)], doppio=inglese)
    open(catalogo, "wb").write(ambiguo)
    controlla("catalog.bin: due record per lo stesso bundle -> 'binario' e nessuna modifica (non si tocca nulla)",
              adapter_unity_loc.crc_del_bundle(bundle)[0] == "binario"
              and adapter_unity_loc.togli_crc(ambiguo, [inglese]) is None)
    open(catalogo, "wb").write(_catalogo_bin([(francese, 1)]))
    controlla("catalog.bin che non nomina il bundle -> 'binario'", adapter_unity_loc.crc_del_bundle(bundle)[0] == "binario")


def main(base, bundle=None):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    # dati dell'utente (profili, memoria) nella cartella di prova
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO
    for d in ("memory", "profiles"):
        os.makedirs(os.path.join(progetto.CARTELLA_PROGETTO, d))
    automatico.CARTELLA_PROFILI = os.path.join(progetto.CARTELLA_PROGETTO, "profiles")
    prova_formato(_cartella(base, "formato"))
    prova_lingue(_cartella(base, "lingue"))
    prova_crc(_cartella(base, "crc"))
    prova_catalogo_bin(_cartella(base, "catalogo-bin"))
    prova_rifiuti(_cartella(base, "rifiuti"))
    if bundle:
        prova_bundle_vero(_cartella(base, "vero"), bundle)
    else:
        print("(nessun bundle vero dato: solo le prove con i bundle finti)")
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


def _cartella(base, nome):
    p = os.path.join(base, nome)
    os.makedirs(p, exist_ok=True)
    return p


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None) else 1)
