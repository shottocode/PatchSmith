"""Test del configuratore e della scheda di Kingdom Hearts HD 1.5+2.5 (lingua predefinita negli eseguibili).

Uso: python -I test/test_configurator.py <cartella_temporanea_vuota> [<cartella del gioco vero>]
Costruisce un'installazione finta (eseguibili PE finti, indice .hed finto) e prova: riconoscimento, consenso,
scrittura tutto-o-niente, rifiuti, aggiornamento del gioco, ripristino identico.
Con la cartella del gioco vero, copia SOLO i suoi eseguibili e un indice nella cartella temporanea e ripete la
prova sulle copie: la cartella del gioco non viene mai toccata e nessun programma viene avviato.
"""
import hashlib
import os
import shutil
import stat
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (adapter_webjson, auto as automatico, cli, configurator as configuratore,
                        official_language as lingua_ufficiale, project as progetto, game_kingdom_hearts as kh,
                        system as sistema, steam)

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def impronta(percorso):
    with open(percorso, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def impronte(cartella):
    return {n: impronta(os.path.join(cartella, n)) for n in kh.ESEGUIBILI if os.path.exists(os.path.join(cartella, n))}


def pe_finto(valore=1, doppio=False, senza_confronto=False, firma=b""):
    """PE64 minimo: .text con lo schema della lingua predefinita e il confronto con "english", .rdata con la
    tabella delle lingue."""
    testo = bytearray(0x400)
    schema = (b"\xBB" + bytes([valore]) + b"\x00\x00\x00\x48\x83\x3D\x11\x22\x33\x44\x00\x75\x31\x8B\xCB\xE8"
              + b"\x00\x00\x00\x00")
    testo[0x10:0x10 + len(schema)] = schema
    if doppio:
        testo[0x200:0x200 + len(schema)] = schema
    rdata = bytearray(0x200)
    rdata[0x40:0x40 + len(kh.TABELLA)] = kh.TABELLA
    k = 0x10 + 0x63
    if not senza_confronto:
        testo[k:k + 7] = b"\x48\x8D\x15" + struct.pack("<i", (0x2000 + 0x40) - (0x1000 + k + 7))
    testa = bytearray(0x400)
    testa[0:2] = b"MZ"
    struct.pack_into("<I", testa, 0x3C, 0x80)
    testa[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", testa, 0x84, 0x8664, 2, 0, 0, 0, 0xF0, 0x22)
    struct.pack_into("<H", testa, 0x98, 0x20B)
    struct.pack_into("<Q", testa, 0x98 + 24, 0x140000000)
    sezioni = 0x98 + 0xF0
    for i, (nome, rva, foff, dim, car) in enumerate(((b".text", 0x1000, 0x400, 0x400, 0x60000020),
                                                     (b".rdata", 0x2000, 0x800, 0x200, 0x40000040))):
        s = sezioni + 40 * i
        testa[s:s + 8] = nome.ljust(8, b"\x00")
        struct.pack_into("<IIII", testa, s + 8, dim, rva, dim, foff)
        struct.pack_into("<I", testa, s + 36, car)
    return bytes(testa) + bytes(testo) + bytes(rdata) + firma


def crea_gioco(cartella):
    os.makedirs(os.path.join(cartella, "Image", "dt"))
    voci = b"".join(hashlib.md5(n.encode()).digest() + b"\x00" * 16 for n in
                    ("msg/us/sys.bar", "msg/it/sys.bar", "msg/fr/sys.bar", "msg/us/al.bar"))
    open(os.path.join(cartella, "Image", "dt", "kh2_first.hed"), "wb").write(voci)
    for n in kh.ESEGUIBILI:
        open(os.path.join(cartella, n), "wb").write(pe_finto(firma=n.encode()))
    open(os.path.join(cartella, "KINGDOM HEARTS HD 1.5+2.5 ReMIX.exe"), "wb").write(b"MZ protezione")


def diff_byte(a, b):
    with open(a, "rb") as f:
        x = f.read()
    with open(b, "rb") as f:
        y = f.read()
    return [i for i in range(min(len(x), len(y))) if x[i] != y[i]] if len(x) == len(y) else None


def main(base, gioco_vero=None):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    automatico.CARTELLA_PROFILI = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatico.CARTELLA_PROFILI)
    steam.cartella_steam = lambda: None
    sistema.gioco_aperto = lambda c: False
    configuratore.smart_app_control = lambda: 0
    salvataggi = os.path.join(base, "Documenti", "My Games", kh.NOME, "Steam", "123")
    os.makedirs(salvataggi)
    open(os.path.join(salvataggi, "KHFM_WW.png"), "wb").write(b"salvataggio")
    kh.SALVATAGGI = os.path.dirname(os.path.dirname(salvataggi))

    sopra = os.path.join(base, "giochi", "kingdom hearts hd")
    gioco = os.path.join(sopra, "KH")
    crea_gioco(gioco)
    originali = impronte(gioco)
    protezione = impronta(os.path.join(gioco, "KINGDOM HEARTS HD 1.5+2.5 ReMIX.exe"))

    # --- riconoscimento
    controlla("scheda: riconosciuta dalla cartella del gioco e da quella sopra",
              kh.riconosci(gioco) == gioco and kh.riconosci(sopra) == gioco and kh.riconosci(base) is None)
    controlla("scheda: lingue dei testi installate (dall'indice .hed)", kh.lingue_presenti(gioco) == {"en", "it", "fr"})
    controlla("scheda: punto della lingua trovato e verificato",
              kh.punto_lingua(pe_finto())[1] == 1 and kh.punto_lingua(pe_finto(6))[1] == 6)
    for nome, dati in (("doppio", pe_finto(doppio=True)), ("senza confronto con 'english'",
                                                           pe_finto(senza_confronto=True)),
                       ("non PE", b"ciao" * 100)):
        try:
            kh.punto_lingua(dati)
            controlla("scheda: eseguibile %s -> rifiutato" % nome, False)
        except kh.ErroreScheda:
            controlla("scheda: eseguibile %s -> rifiutato" % nome, True)
    r = lingua_ufficiale.rileva(gioco, "it")
    controlla("rilevamento: italiano presente dai file del gioco (senza Steam)",
              r["presente"] is True and r["motore"]["tipo"] == "scheda")

    # --- senza consenso: solo la proposta, nulla di scritto
    percorso, esito = automatico.prepara(sopra, "auto", "it", "finto", silenzio)
    controlla("senza consenso: proposta, nessun file toccato",
              percorso is None and esito.get("serve_consenso") and impronte(gioco) == originali
              and any("1 byte" in x and gioco in x for x in esito["lingua_ufficiale_righe"])
              and not any("Non posso impostarla io" in x for x in esito["lingua_ufficiale_righe"]))
    finto = dict(r, steam_conosciuto={"appid": "1", "nome": "x", "eseguibile": "x.exe"})
    controlla("messaggio: se la scheda spiega il rifiuto, niente 'Non posso impostarla io' contraddittorio",
              not any("Non posso" in x for x in lingua_ufficiale.messaggio(finto, "it", ["motivo e rimedio"], False)))

    # --- rifiuti: niente scritto
    configuratore.smart_app_control = lambda: 1
    fatto, righe = lingua_ufficiale.imposta(gioco, r, "it", silenzio, consenso=True)
    controlla("Smart App Control attivo -> rifiuto prima di chiedere il consenso, nessun file toccato",
              fatto is False and impronte(gioco) == originali and any("Smart App Control" in x for x in righe)
              and not configuratore.ha_consenso(gioco))
    configuratore.smart_app_control = lambda: 0
    sistema.gioco_aperto = lambda c: True
    fatto, righe = lingua_ufficiale.imposta(gioco, r, "it", silenzio, consenso=True)
    controlla("gioco aperto -> rifiuto, nessun file toccato, salvataggi non copiati, consenso ricordato",
              fatto is False and impronte(gioco) == originali and not configuratore.nota(gioco, "salvataggi_copiati")
              and configuratore.ha_consenso(gioco))
    sistema.gioco_aperto = lambda c: False
    quarto = os.path.join(gioco, kh.ESEGUIBILI[3])
    os.chmod(quarto, stat.S_IREAD)
    fatto, righe = lingua_ufficiale.imposta(gioco, r, "it", silenzio, consenso=True)
    os.chmod(quarto, stat.S_IREAD | stat.S_IWRITE)
    controlla("un eseguibile di sola lettura -> nessuno modificato",
              fatto is False and impronte(gioco) == originali and not configuratore.leva(gioco, "lingua"))
    vera_scrittura = adapter_webjson.scrivi_atomico
    for caso, finale in (("scrittura del terzo eseguibile", ".exe"), ("backup del terzo eseguibile", progetto.SUFFISSO_INFO)):
        chiamate = []

        def scrittura_che_si_rompe(percorso, dati, finale=finale, chiamate=chiamate):
            if percorso.endswith(finale) and "patchsmith" + os.sep + "configurazioni" not in percorso:
                chiamate.append(percorso)
                if len(chiamate) == 3:
                    raise OSError("disco pieno (simulato)")
            vera_scrittura(percorso, dati)
        adapter_webjson.scrivi_atomico = scrittura_che_si_rompe
        fatto, righe = lingua_ufficiale.imposta(gioco, r, "it", silenzio, consenso=True)
        adapter_webjson.scrivi_atomico = vera_scrittura
        controlla("guasto durante %s -> tutto com'era (file, backup, registro)" % caso,
                  fatto is False and len(chiamate) >= 3 and impronte(gioco) == originali
                  and not configuratore.leva(gioco, "lingua")
                  and not [n for n in os.listdir(gioco) if "patchsmith" in n])
    controlla("il consenso dato resta anche se la modifica non e' riuscita", configuratore.ha_consenso(gioco))

    # --- con consenso
    percorso, esito = automatico.prepara(sopra, "auto", "it", "finto", silenzio, consenso=True)
    cambi = {n: diff_byte(os.path.join(gioco, n), os.path.join(gioco, n + progetto.SUFFISSO_BACKUP))
             for n in kh.ESEGUIBILI}
    controlla("con consenso: 1 byte cambiato in ognuno dei 6 eseguibili (valore 6 = italiano), con backup",
              esito.get("lingua_impostata") is True and all(c == [0x411] for c in cambi.values())
              and all(kh.punto_lingua(open(os.path.join(gioco, n), "rb").read())[1] == 6 for n in kh.ESEGUIBILI))
    controlla("protezione e altri file non toccati",
              impronta(os.path.join(gioco, "KINGDOM HEARTS HD 1.5+2.5 ReMIX.exe")) == protezione)
    controlla("consenso salvato per il gioco, registro della leva, salvataggi copiati",
              configuratore.ha_consenso(gioco) and configuratore.leva(gioco, "lingua")["stato"] == "applicata"
              and os.path.isdir(str(configuratore.nota(gioco, "salvataggi_copiati"))))
    controlla("messaggio: attivata, senza 'Non posso impostarla io' e senza 'traduzione installata'",
              any("Ho attivato" in x for x in esito["lingua_ufficiale_righe"])
              and not any("Non posso" in x for x in esito["lingua_ufficiale_righe"])
              and not esito.get("traduzione_installata"))
    dopo = impronte(gioco)
    percorso, esito = automatico.prepara(gioco, "auto", "it", "finto", silenzio)
    controlla("seconda analisi: gia' attivo, niente riscritto",
              esito.get("lingua_impostata") is True and impronte(gioco) == dopo
              and any("gia' attivo" in x for x in esito["lingua_ufficiale_righe"]))

    # --- aggiornamento del gioco: un eseguibile torna originale ma diverso; col consenso salvato si riapplica
    nuovo = pe_finto(firma=b"versione 2")
    open(os.path.join(gioco, kh.ESEGUIBILI[1]), "wb").write(nuovo)
    percorso, esito = automatico.prepara(gioco, "auto", "it", "finto", silenzio)
    p1 = os.path.join(gioco, kh.ESEGUIBILI[1])
    controlla("aggiornamento: riapplicato da solo (consenso gia' dato), il nuovo originale ha il suo backup",
              esito.get("lingua_impostata") is True and kh.punto_lingua(open(p1, "rb").read())[1] == 6
              and open(p1 + progetto.SUFFISSO_BACKUP, "rb").read() == nuovo
              and os.path.exists(p1 + progetto.SUFFISSO_BACKUP + ".1"))

    # --- ripristino
    sistema.gioco_aperto = lambda c: True
    controlla("ripristino con il gioco aperto -> non tocca nulla", automatico.ripristina(gioco, silenzio) is False
              and kh.punto_lingua(open(p1, "rb").read())[1] == 6)
    sistema.gioco_aperto = lambda c: False
    automatico.ripristina(sopra, silenzio)
    attese = dict(originali, **{kh.ESEGUIBILI[1]: hashlib.sha256(nuovo).hexdigest()})
    controlla("Ripristina originali: eseguibili identici (l'aggiornato al suo nuovo originale), niente backup "
              "rimasti, leva tolta, consenso conservato",
              impronte(gioco) == attese and not [n for n in os.listdir(gioco) if ".patchsmith" in n]
              and not configuratore.leva(gioco, "lingua") and configuratore.ha_consenso(gioco)
              and not lingua_ufficiale.impostata(gioco))
    controlla("riga di comando: auto con il consenso gia' dato riattiva ed esce con 0",
              cli.main(["auto", gioco, "--a", "it", "--backend", "finto"]) == 0
              and all(kh.punto_lingua(open(os.path.join(gioco, n), "rb").read())[1] == 6 for n in kh.ESEGUIBILI))
    controlla("riga di comando: ripristina con la cartella", cli.main(["ripristina", gioco]) == 0
              and impronte(gioco) == attese)

    # --- casi trovati dalla revisione
    automatico.prepara(gioco, "auto", "it", "finto", silenzio)  # consenso gia' dato: attiva
    nuovo2 = pe_finto(firma=b"versione 3")
    p2 = os.path.join(gioco, kh.ESEGUIBILI[2])
    open(p2, "wb").write(nuovo2)
    controlla("eseguibile aggiornato dal gioco, poi Ripristina: chiuso al primo colpo, resta il file nuovo",
              automatico.ripristina(gioco, silenzio) is True and not configuratore.leva(gioco, "lingua")
              and open(p2, "rb").read() == nuovo2 and not [n for n in os.listdir(gioco) if ".patchsmith" in n])
    attese[kh.ESEGUIBILI[2]] = hashlib.sha256(nuovo2).hexdigest()

    automatico.prepara(gioco, "auto", "it", "finto", silenzio)
    italiano = impronte(gioco)
    chiamate = []

    def rotta_al_terzo(percorso, dati):
        if percorso.endswith(".exe"):
            chiamate.append(percorso)
            if len(chiamate) == 3:
                raise OSError("file in uso (simulato)")
        vera_scrittura(percorso, dati)
    adapter_webjson.scrivi_atomico = rotta_al_terzo
    fatto, righe = lingua_ufficiale.imposta(gioco, lingua_ufficiale.rileva(gioco, "fr"), "fr", silenzio, consenso=True)
    adapter_webjson.scrivi_atomico = vera_scrittura
    controlla("guasto passando al francese -> si torna all'italiano di prima (non all'originale), leva conservata",
              fatto is False and impronte(gioco) == italiano and configuratore.leva(gioco, "lingua")["valore"] == "it"
              and any("com'era prima" in x for x in righe))

    automatico.ripristina(gioco, silenzio)
    adesso = impronte(gioco)

    def interrotta(percorso, dati):
        if percorso.endswith(kh.ESEGUIBILI[3]):
            raise KeyboardInterrupt
        vera_scrittura(percorso, dati)
    adapter_webjson.scrivi_atomico = interrotta
    try:
        lingua_ufficiale.imposta(gioco, lingua_ufficiale.rileva(gioco, "it"), "it", silenzio, consenso=True)
        passata = False
    except KeyboardInterrupt:
        passata = True
    adapter_webjson.scrivi_atomico = vera_scrittura
    controlla("interruzione (Ctrl+C) durante la scrittura -> file com'erano, nessuna leva a meta'",
              passata and impronte(gioco) == adesso and not configuratore.leva(gioco, "lingua")
              and not [n for n in os.listdir(gioco) if ".patchsmith" in n])

    automatico.prepara(gioco, "auto", "it", "finto", silenzio)
    registro = configuratore.leggi(gioco)
    registro["leve"]["lingua"]["stato"] = "in_corso"  # come se il processo fosse morto a meta'
    configuratore._salva(gioco, registro)
    messaggi = []
    fatto, righe = lingua_ufficiale.imposta(gioco, lingua_ufficiale.rileva(gioco, "it"), "it", messaggi.append)
    controlla("operazione rimasta a meta': la volta dopo rimette gli originali e riapplica",
              any("interrotta" in m for m in messaggi) and fatto is True
              and configuratore.leva(gioco, "lingua")["stato"] == "applicata")

    def bloccata(percorso, dati):
        if percorso.endswith(kh.ESEGUIBILI[4]):
            raise PermissionError("in uso da un altro programma (simulato)")
        vera_scrittura(percorso, dati)
    adapter_webjson.scrivi_atomico = bloccata
    messaggi = []
    esito_r = automatico.ripristina(gioco, messaggi.append)
    adapter_webjson.scrivi_atomico = vera_scrittura
    valori = {n: kh.punto_lingua(open(os.path.join(gioco, n), "rb").read())[1] for n in kh.ESEGUIBILI}
    controlla("Ripristina con un eseguibile bloccato: gli altri tornano originali, avviso e risultato False",
              esito_r is False and valori[kh.ESEGUIBILI[4]] == 6
              and all(v == 1 for n, v in valori.items() if n != kh.ESEGUIBILI[4])
              and any("Restano da rimettere" in m for m in messaggi))
    controlla("... ripetendo si completa", automatico.ripristina(gioco, silenzio) is True and impronte(gioco) == attese)

    una_cartella = os.path.join(base, "una cartella")
    os.makedirs(una_cartella)
    try:
        adapter_webjson.scrivi_atomico(una_cartella, b"x")
    except OSError:
        pass
    controlla("scrittura atomica fallita: nessun temporaneo lasciato",
              not os.path.exists(una_cartella + adapter_webjson.SUFFISSO_TMP))
    sola = os.path.join(base, "sola lettura.json")
    open(sola, "wb").write(b"{}")
    os.chmod(sola, stat.S_IREAD)
    progetto.crea_backup(sola)
    controlla("backup di un file di sola lettura: riuscito e il backup resta scrivibile",
              os.access(sola + progetto.SUFFISSO_BACKUP, os.W_OK))
    os.chmod(sola, stat.S_IREAD | stat.S_IWRITE)

    automatico.prepara(gioco, "auto", "it", "finto", silenzio)
    percorso, esito = automatico.prepara(gioco, "auto", "it", "finto", silenzio, traduci_comunque=True)
    controlla("'traduci comunque' senza nulla da tradurre: la lingua attivata resta",
              percorso is None and configuratore.leva(gioco, "lingua")
              and kh.punto_lingua(open(os.path.join(gioco, kh.ESEGUIBILI[0]), "rb").read())[1] == 6)
    automatico.ripristina(gioco, silenzio)
    configuratore.smart_app_control = lambda: 1
    controlla("riga di comando: attivazione rifiutata con --modifica-eseguibili -> uscita 2",
              cli.main(["auto", gioco, "--a", "it", "--backend", "finto", "--modifica-eseguibili"]) == 2)
    configuratore.smart_app_control = lambda: 0

    doppia = os.path.join(base, "due copie")
    crea_gioco(os.path.join(doppia, "copia A"))
    crea_gioco(os.path.join(doppia, "copia B"))
    prima = {d: impronte(os.path.join(doppia, d)) for d in ("copia A", "copia B")}
    percorso, esito = automatico.prepara(doppia, "auto", "it", "finto", silenzio, consenso=True)
    controlla("due copie del gioco nella cartella: nessuna modifica, chiede di sceglierne una",
              esito.get("lingua_impostata") is False and any("2 copie" in x for x in esito["lingua_ufficiale_righe"])
              and all(impronte(os.path.join(doppia, d)) == prima[d] for d in prima))

    import test_official_language as plu
    radice_steam, libreria = os.path.join(base, "Steam"), os.path.join(base, "Libreria")
    os.makedirs(os.path.join(radice_steam, "appcache"))
    os.makedirs(os.path.join(radice_steam, "steamapps"))
    open(os.path.join(radice_steam, "steamapps", "libraryfolders.vdf"), "w").write(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % libreria.replace("\\", "\\\\"))
    open(os.path.join(radice_steam, "appcache", "appinfo.vdf"), "wb").write(plu.appinfo({2552430: {
        "common": {"name": kh.NOME, "type": "Game", "supported_languages": {"english": {"supported": "true"},
                                                                           "italian": {"supported": "true"}}},
        "depots": {"baselanguages": "english,italian"}}}))
    comune = os.path.join(libreria, "steamapps", "common")
    crea_gioco(os.path.join(comune, "KH Steam"))
    open(os.path.join(libreria, "steamapps", "appmanifest_2552430.acf"), "w").write(
        '"AppState"\n{\n\t"appid"\t\t"2552430"\n\t"name"\t\t"%s"\n\t"installdir"\t\t"KH Steam"\n}\n' % kh.NOME)
    steam.cartella_steam = lambda: radice_steam
    steam._CACHE.clear()
    prima = impronte(os.path.join(comune, "KH Steam"))
    percorso, esito = automatico.prepara(comune, "auto", "it", "finto", silenzio, consenso=True)
    steam.cartella_steam = lambda: None
    controlla("installato da Steam (scelta la cartella sopra): menu Lingua di Steam, eseguibili non toccati",
              any("Proprieta' > Generali > Lingua" in x for x in esito.get("lingua_ufficiale_righe", []))
              and impronte(os.path.join(comune, "KH Steam")) == prima)

    # --- un eseguibile di una versione sconosciuta: nessuno viene modificato
    altro = os.path.join(base, "KH2")
    crea_gioco(altro)
    open(os.path.join(altro, kh.ESEGUIBILI[2]), "wb").write(pe_finto(senza_confronto=True))
    prima = impronte(altro)
    fatto, righe = lingua_ufficiale.imposta(altro, lingua_ufficiale.rileva(altro, "it"), "it", silenzio, consenso=True)
    controlla("versione sconosciuta di un eseguibile -> nessun file modificato, spiegazione",
              fatto is False and impronte(altro) == prima and any("Non riconosco" in x for x in righe))

    # --- prova sulle copie degli eseguibili veri (la cartella del gioco non viene toccata)
    if gioco_vero:
        copia = os.path.join(base, "copia del gioco")
        os.makedirs(os.path.join(copia, "Image", "dt"))
        shutil.copy2(os.path.join(gioco_vero, "Image", "dt", "kh2_first.hed"), os.path.join(copia, "Image", "dt"))
        for n in kh.ESEGUIBILI:
            if os.path.exists(os.path.join(gioco_vero, n)):
                shutil.copy2(os.path.join(gioco_vero, n), copia)
        veri = impronte(copia)
        controlla("gioco vero (copie): lingue dei testi installate", kh.lingue_presenti(copia) >= {"en", "it"})
        r = lingua_ufficiale.rileva(copia, "it")
        fatto, righe = lingua_ufficiale.imposta(copia, r, "it", silenzio, consenso=True)
        cambi = {n: diff_byte(os.path.join(copia, n), os.path.join(copia, n + progetto.SUFFISSO_BACKUP))
                 for n in veri}
        punti = {n: kh.punto_lingua(open(os.path.join(copia, n), "rb").read()) for n in veri}
        controlla("gioco vero (copie): esattamente 1 byte per eseguibile, ora vale 6 (italiano)",
                  fatto is True and len(veri) == 6 and all(c and len(c) == 1 and c[0] == punti[n][0]
                                                           for n, c in cambi.items())
                  and all(v == 6 for _, v in punti.values()))
        for n, (o, _) in sorted(punti.items()):
            print("        %-45s byte 0x%X" % (n, o))
        automatico.ripristina(copia, silenzio)
        controlla("gioco vero (copie): ripristino identico", impronte(copia) == veri)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None) else 1)
