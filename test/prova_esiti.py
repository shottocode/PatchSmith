"""Esito strutturato delle operazioni (esito.py), finestre dell'interfaccia per ogni esito e "Crea rapporto".

Uso: python -I test/prova_esiti.py <cartella_temporanea_vuota>
Solo giochi finti creati qui (web, GameMaker, Unity Mono/IL2CPP/Localization) e il traduttore di prova o un blocco
di Google scritto in uno services.json di prova: nessuna rete, nessun gioco vero. Dati di PatchSmith, cartella
dell'utente e %TEMP% stanno tutti nella cartella della prova.
"""
import base64
import contextlib
import io
import json
import os
import sys
import time
import tokenize
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import gioco_web_finto  # noqa: E402
import prova_gamemaker  # noqa: E402  (gioco GameMaker finto)
import prova_unity  # noqa: E402  (gioco Unity finto e componenti finti)
from patchsmith import __version__, adapter_unity, automatico, backend, esito, ollama, progetto, rapporto  # noqa: E402
from patchsmith import rilevamento, sistema  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def impronte(cartella):
    """{percorso relativo: (dimensione, data)} di tutti i file: per vedere che nulla e' stato toccato."""
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for f in files:
            s = os.stat(os.path.join(radice, f))
            uscita[os.path.relpath(os.path.join(radice, f), cartella)] = (s.st_size, s.st_mtime_ns)
    return uscita


def gioco_unity_il2cpp(cartella, tabelle=False, crc=0):
    """Gioco Unity IL2CPP finto; con tabelle=True anche il bundle inglese di Unity Localization e il catalogo di
    Addressables (crc: il valore di m_Crc nelle opzioni dei bundle)."""
    prova_unity.crea_gioco(cartella)
    os.rename(os.path.join(cartella, "Gioco_Data", "Managed"), os.path.join(cartella, "Gioco_Data", "nonusata"))
    os.makedirs(os.path.join(cartella, "Gioco_Data", "il2cpp_data", "Metadata"))
    open(os.path.join(cartella, "Gioco_Data", "il2cpp_data", "Metadata", "global-metadata.dat"), "wb").write(
        b"\xaf\x1b\xb1\xfa" + b"\x00" * 64)
    open(os.path.join(cartella, "GameAssembly.dll"), "wb").write(b"MZ finto")
    if tabelle:
        aa = os.path.join(cartella, "Gioco_Data", "StreamingAssets", "aa")
        os.makedirs(os.path.join(aa, "StandaloneWindows64"))
        open(os.path.join(aa, "StandaloneWindows64", "localization-string-tables-english(en)_assets_all.bundle"),
             "wb").write(b"finto")
        opzioni = '{"m_Hash":"8f2c","m_Crc":%d,"m_Timeout":0}' % crc
        json.dump({"m_LocatorId": "AddressablesMainContentCatalog", "m_InternalIds": [],
                   "m_ExtraDataString": base64.b64encode(b"\x07\x00" + opzioni.encode("utf-16-le")).decode("ascii")},
                  open(os.path.join(aa, "catalog.json"), "w"))


def gioco_gamemaker(cartella, italiano=False):
    os.makedirs(os.path.join(cartella, "Localization", "french"))
    for rel, testo in (("Localization/english_ui.txt", prova_gamemaker.UI),
                       ("Localization/english_npc.txt", prova_gamemaker.NPC),
                       ("Localization/french/ui.txt", prova_gamemaker.FR_UI)):
        open(os.path.join(cartella, *rel.split("/")), "wb").write(testo.encode("utf-8"))
    latino = set(chr(c) for c in range(32, 127)) | set("àèéùÀÈÉÙíóúÍÓÚ")
    open(os.path.join(cartella, "data.win"), "wb").write(prova_gamemaker.data_win([("fn_testo", latino)]))
    open(os.path.join(cartella, "Gioco.exe"), "wb").write(b"MZ")
    if italiano:
        os.makedirs(os.path.join(cartella, "Localization", "italian"))
        open(os.path.join(cartella, "Localization", "italian", "ui.txt"), "wb").write(
            b"0 Combatti\r\n1 Cambia\r\n2 Cattura\r\n3 Fuggi\r\n4 Cosa deve fare \r\n5  ora?\r\n")


def esito_di(dizionario):
    return dizionario.get("esito") if isinstance(dizionario, dict) else None


def senza_smith(testo):
    return "smith.py" not in testo and "python -I" not in testo


class MezzoTraduttore(backend.Finto):
    """Traduttore di prova che non traduce i testi con 'lantern': restano in originale."""

    def traduci_righe(self, righe, origine, destinazione, **_):
        return ["" if "lantern" in r.lower() else "[it] " + r for r in righe]


def prova_web(base):
    gioco = os.path.join(base, "web", "Lantern Keeper")
    gioco_web_finto.crea(gioco)
    prima = impronte(gioco)
    percorso, completato, d = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)
    e = esito_di(d)
    controlla("web tradotto: esito OK, scritto, motore web, testi contati, valori di prima invariati",
              completato and percorso and e and e.codice == "OK" and e.scritto and e.motore == "web"
              and e.tradotti > 10 and e.rimasti == 0)
    f = esito.finestra(e, "traduci")
    controlla("web tradotto: finestra 'Traduzione installata' (prima non c'era nessuna finestra)",
              f["titolo"] == "Traduzione installata" and "Avvia il gioco" in f["testo"]
              and "Testi tradotti: %d." % e.tradotti in f["testo"] and f["pulsanti"] == [("OK", None)])

    d = {}
    fatto = automatico.riapplica(gioco, silenzio, prova=True, esito=d)
    controlla("riapplica: esito OK e finestra 'Traduzione riapplicata'", fatto and esito_di(d).codice == "OK"
              and esito.finestra(esito_di(d), "riapplica")["titolo"] == "Traduzione riapplicata")

    # gioco aperto: ripristino rifiutato, nessun file toccato
    vero = sistema.gioco_aperto
    sistema.gioco_aperto = lambda c: True
    tradotto = impronte(gioco)
    d = {}
    try:
        fatto = automatico.ripristina(gioco, silenzio, esito=d)
    finally:
        sistema.gioco_aperto = vero
    e = esito_di(d)
    f = esito.finestra(e, "ripristina")
    controlla("ripristino con il gioco aperto: GIOCO_APERTO, nessun file toccato, 'Riprova'",
              not fatto and e.codice == "GIOCO_APERTO" and impronte(gioco) == tradotto
              and f["titolo"] == "Il gioco è aperto" and ("Riprova", "chiudi_gioco") in f["pulsanti"])

    d = {}
    controlla("ripristino: esito OK, 'Originali ripristinati'", automatico.ripristina(gioco, silenzio, esito=d)
              and esito_di(d).codice == "OK" and esito.finestra(esito_di(d), "ripristina")["titolo"]
              == "Originali ripristinati" and impronte(gioco).keys() == prima.keys())
    d = {}
    automatico.ripristina(gioco, silenzio, esito=d)
    controlla("di nuovo ripristino: GIA_ORIGINALE", esito_di(d).codice == "GIA_ORIGINALE"
              and esito.finestra(esito_di(d), "ripristina")["titolo"] == "Gioco già originale")

    vuota = os.path.join(base, "nessun-gioco")
    os.makedirs(vuota)
    d = {}
    controlla("riapplica senza traduzioni salvate: NULLA_DA_FARE",
              not automatico.riapplica(vuota, silenzio, esito=d) and esito_di(d).codice == "NULLA_DA_FARE")

    # parte dei testi non tradotta: PARZIALE con i testi rimasti da mostrare (memoria di prova da zero)
    with open(automatico.trova_profilo(gioco), encoding="utf-8") as f:
        os.remove(progetto.percorso_memoria(json.load(f), prova=True))
    vero = automatico.crea_backend
    automatico.crea_backend = lambda nome, log=print: MezzoTraduttore()
    try:
        _, completato, d = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)
    finally:
        automatico.crea_backend = vero
    e = esito_di(d)
    f = esito.finestra(e, "traduci")
    controlla("testi rimasti: PARZIALE, scritto, con i primi originali rimasti e 'Mostra i rimasti'",
              not completato and e.codice == "PARZIALE" and e.scritto and e.rimasti > 0
              and any("antern" in t for t in e.esempi_rimasti) and ("Mostra i rimasti", "mostra_rimasti")
              in f["pulsanti"] and f["titolo"] == "Traduzione installata in parte"
              and "rimasti in originale: %d" % e.rimasti in f["testo"])
    controlla("testi rimasti: elenco leggibile", "Testi rimasti in originale: %d" % e.rimasti
              in esito.testo_rimasti(e) and "- " in esito.testo_rimasti(e))
    automatico.ripristina(gioco, silenzio)
    return gioco


def prova_google_bloccato(base, gioco):
    """Google in pausa per un blocco (scritto nello stato dei traduttori di prova): nessuna richiesta, esito chiaro."""
    stato = progetto.cartella_dati("services.json")
    adesso = time.time()
    with open(stato, "w", encoding="utf-8") as f:
        json.dump({"versione": 1, "google": {"richieste": [], "blocco": {
            "dal": adesso - 60, "fino": adesso + 86400, "livello": 1, "tipo": "a_freddo", "http": 429,
            "dettaglio": None, "disattivato": False}}}, f)
    # la memoria del traduttore di prova (.prova.json) non vale per Google: servirebbero richieste, che la pausa
    # impedisce prima di mandarne una
    _, completato, d = automatico.analizza_e_traduci(gioco, "auto", "it", "google", silenzio)
    e = esito_di(d)
    f = esito.finestra(e, "traduci", locale_pronto=False, attuale="google")
    controlla("Google in pausa: TRADUTTORE_BLOCCATO, con il motivo e il traduttore locale da installare",
              not completato and e and e.codice == "TRADUTTORE_BLOCCATO" and "Google" in e.motivo
              and not e.scritto and ("Installa il traduttore locale", "installa_locale") in f["pulsanti"]
              and f["titolo"] == "Traduzione interrotta")
    controlla("Google in pausa: nessun riferimento alla riga di comando", senza_smith(f["testo"] + e.motivo))
    f = esito.finestra(e, "traduci", locale_pronto=True, attuale="google")
    controlla("... con il traduttore locale installato: 'Usa il traduttore locale'",
              ("Usa il traduttore locale", "usa_locale") in f["pulsanti"])
    os.remove(stato)
    automatico.ripristina(gioco, silenzio)


def prova_gamemaker_aperto(base):
    gioco = os.path.join(base, "gm", "Gioco GM")
    gioco_gamemaker(gioco)
    prima = impronte(gioco)
    veri = sistema.gioco_aperto, progetto.gioco_aperto
    sistema.gioco_aperto = progetto.gioco_aperto = lambda c: True
    try:
        _, completato, d = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)
    finally:
        sistema.gioco_aperto, progetto.gioco_aperto = veri
    e = esito_di(d)
    f = esito.finestra(e, "traduci")
    controlla("GameMaker con il gioco aperto: GIOCO_APERTO, niente scritto, nessun file del gioco cambiato",
              not completato and e.codice == "GIOCO_APERTO" and not e.scritto and impronte(gioco) == prima
              and "riapplica" in e.azioni and f["titolo"] == "Il gioco è aperto")

    italiano = os.path.join(base, "gm-it", "Gioco GM")
    gioco_gamemaker(italiano, italiano=True)
    prima = impronte(italiano)
    percorso, completato, d = automatico.analizza_e_traduci(italiano, "auto", "it", "finto", silenzio)
    e = esito_di(d)
    controlla("GameMaker con l'italiano ufficiale: UFFICIALE (con le righe per la domanda sulla lingua ufficiale), "
              "nulla toccato", not percorso and e.codice == "UFFICIALE" and e.extra.get("lingua_ufficiale")
              and e.extra.get("righe") and impronte(italiano) == prima and "Italiano" in e.motivo)
    # se la lingua ufficiale sfugge al primo controllo, la trova la ricerca dei file di GameMaker
    _, d = automatico._prepara_gamemaker(italiano, None, "auto", "it", rilevamento.rileva(italiano), silenzio)
    e = esito_di(d)
    f = esito.finestra(e, "traduci")
    controlla("GameMaker, file della lingua di arrivo: UFFICIALE con 'Traduci comunque'", e.codice == "UFFICIALE"
              and ("Traduci comunque", "traduci_comunque") in f["pulsanti"] and impronte(italiano) == prima)


def prova_giochi_unity(base):
    prova_unity.prepara_componenti(os.path.join(base, "componenti"))
    adapter_unity.in_esecuzione = lambda *_: False

    il2cpp = os.path.join(base, "il2cpp", "Gioco")
    gioco_unity_il2cpp(il2cpp)
    prima = impronte(il2cpp)
    percorso, _, d = automatico.analizza_e_traduci(il2cpp, "auto", "it", "finto", silenzio)
    e = esito_di(d)
    f = esito.finestra(e, "traduci")
    controlla("Unity IL2CPP senza tabelle: NON_SUPPORTATO con 'IL2CPP' nel motivo, nulla toccato, 'Crea rapporto'",
              not percorso and e.codice == "NON_SUPPORTATO" and "IL2CPP" in e.motivo and impronte(il2cpp) == prima
              and ("Crea rapporto", "crea_rapporto") in f["pulsanti"] and "Non ho modificato nulla" in f["testo"])

    crc = os.path.join(base, "crc", "Gioco")
    gioco_unity_il2cpp(crc, tabelle=True, crc=2737410034)
    prima = impronte(crc)
    percorso, _, d = automatico.analizza_e_traduci(crc, "auto", "it", "finto", silenzio)
    e = esito_di(d)
    controlla("Unity Localization con CRC: NON_SUPPORTATO per l'impronta, non 'serve BepInEx 6', nulla toccato",
              not percorso and e.codice == "NON_SUPPORTATO" and "BepInEx 6" not in e.motivo and "CRC" in e.motivo
              and e.motore == "unity-localization" and impronte(crc) == prima)

    anticheat = os.path.join(base, "anticheat", "Gioco")
    prova_unity.crea_gioco(anticheat)
    open(os.path.join(anticheat, "BEService.exe"), "wb").write(b"MZ")
    prima = impronte(anticheat)
    percorso, _, d = automatico.analizza_e_traduci(anticheat, "auto", "it", "finto", silenzio)
    e = esito_di(d)
    controlla("Unity con anti-cheat: ANTICHEAT, nulla toccato", not percorso and e.codice == "ANTICHEAT"
              and impronte(anticheat) == prima and esito.finestra(e)["titolo"] == "Gioco con anti-cheat")

    mono = os.path.join(base, "mono", "Gioco")
    prova_unity.crea_gioco(mono)
    percorso, completato, d = automatico.analizza_e_traduci(mono, "auto", "it", "finto", silenzio)
    e = esito_di(d)
    f = esito.finestra(e, "traduci")
    controlla("Unity Mono (prova): OK, XUnity installato, frasi nuove a Google, finestra con ALT+T e 'Frasi nuove'",
              completato and e.codice == "OK" and e.scritto and e.motore == "unity" and e.dal_vivo == "google"
              and "ALT+T" in f["testo"] and "Frasi nuove mentre giochi: XUnity, con Google gratuito" in f["testo"])
    with open(percorso, encoding="utf-8") as fp:
        profilo = json.load(fp)
    controlla("profilo Unity: il traduttore scelto (non piu' 'xunity-google') e le frasi nuove al traduttore di "
              "PatchSmith", profilo.get("backend") == "finto" and profilo.get("dal_vivo") == "servizio")
    return mono


def prova_eccezioni():
    casi = [
        (adapter_unity.ErroreUnity("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO"), "GIOCO_APERTO"),
        (backend.ErroreBackend(backend.NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=backend.COMANDO_INSTALLA),
         "TRADUTTORE_ASSENTE"),
        (backend.ErroreBackend("Google non risponde"), "TRADUTTORE_BLOCCATO"),
        (PermissionError(13, "Accesso negato", "D:/gioco/file.json"), "PERMESSI"),
        (sistema.ErroreSistema("Non riesco a controllare se il gioco e' aperto"), "GIOCO_APERTO"),
        (RuntimeError("Il backup non corrisponde"), "ERRORE"),
        (KeyError("cartella_gioco"), "ERRORE"),
    ]
    codici = [esito.da_eccezione(err).codice for err, _ in casi]
    controlla("errori -> esiti: gioco aperto, traduttore assente/fermo, permessi, errori noti e imprevisti",
              codici == [c for _, c in casi])
    imprevisto = esito.da_eccezione(KeyError("cartella_gioco"))
    f = esito.finestra(imprevisto, "traduci")
    controlla("errore imprevisto: 'Errore imprevisto (KeyError)' e 'Crea rapporto'",
              f["titolo"] == "Errore imprevisto" and "Errore imprevisto (KeyError)" in f["testo"]
              and ("Crea rapporto", "crea_rapporto") in f["pulsanti"] and f["tipo"] == "errore")
    noto = esito.da_eccezione(RuntimeError("Il backup non corrisponde"))
    controlla("errore noto: il suo messaggio, senza il tipo", not noto.extra.get("imprevisto")
              and "RuntimeError" not in esito.finestra(noto)["testo"] and "backup" in esito.finestra(noto)["testo"])
    assente = esito.da_eccezione(casi[1][0])
    f = esito.finestra(assente, "traduci", locale_pronto=False, attuale="locale")
    controlla("traduttore locale non installato: 'Installa il traduttore locale', niente comandi",
              ("Installa il traduttore locale", "installa_locale") in f["pulsanti"] and senza_smith(f["testo"]))
    # DeepL senza chiave con il traduttore locale gia' installato: "Installa" non farebbe nulla, si propone di usarlo
    senza_chiave = esito.da_eccezione(backend.ErroreBackend("Per usare DeepL serve la tua chiave", "TRADUTTORE_ASSENTE"))
    f = esito.finestra(senza_chiave, "traduci", locale_pronto=True, attuale="deepl")
    controlla("traduttore scelto non pronto, locale installato: 'Usa il traduttore locale' (non 'Installa')",
              ("Usa il traduttore locale", "usa_locale") in f["pulsanti"]
              and not any(a == "installa_locale" for _, a in f["pulsanti"]))
    f = esito.finestra(assente, "traduci", locale_pronto=True, attuale="locale")
    controlla("traduttore locale scelto ma il suo modello manca: resta 'Installa il traduttore locale'",
              ("Installa il traduttore locale", "installa_locale") in f["pulsanti"])
    # operazioni che non toccano il gioco: niente "Riprova" (ripeterebbe l'ultima operazione sul gioco) ne' consigli
    # sul gioco aperto o sulla cartella Programmi
    permessi = esito.da_eccezione(PermissionError(13, "Accesso negato", "C:/Utente/Desktop/rapporto.zip"))
    f = esito.finestra(permessi, "rapporto")
    controlla("rapporto non creato: finestra sua, con il motivo, senza 'Riprova' ne' 'Crea rapporto'",
              f["titolo"] == "Rapporto non creato" and "Accesso negato" in f["testo"] and f["tipo"] == "errore"
              and f["pulsanti"] == [("OK", None)] and "gioco" not in f["testo"])
    f = esito.finestra(permessi, "installa")
    controlla("installazione del traduttore locale con file bloccati: senza 'Riprova' e senza consigli sul gioco",
              not any(a == "chiudi_gioco" for _, a in f["pulsanti"]) and "Programmi" not in f["testo"]
              and "Accesso negato" in f["testo"])


def prova_finestre():
    """Ogni codice ha la sua finestra: titolo proprio, testo senza riga di comando, pulsanti validi."""
    titoli = {}
    for codice in esito.CODICI:
        e = esito.Esito(codice, "Motivo di prova.", motore="gamemaker", scritto=codice in ("OK", "PARZIALE"),
                        tradotti=10, rimasti=2 if codice == "PARZIALE" else 0, esempi_rimasti=["Start", "Quit"],
                        azioni=esito._azioni_per(codice))
        f = esito.finestra(e, "traduci")
        titoli[codice] = f["titolo"]
        ok = f["testo"] and senza_smith(f["testo"]) and f["pulsanti"][0] == ("OK", None) and \
            all(a is None or a in esito.AZIONI for _, a in f["pulsanti"]) and f["tipo"] in (
                "info", "avviso", "errore")
        if not ok:
            controlla("finestra per %s" % codice, False)
    controlla("una finestra per ogni codice, con titoli tutti diversi", len(set(titoli.values())) == len(titoli))
    e = esito.Esito("PARZIALE", motore="gamemaker", scritto=True, tradotti=5, rimasti=1, esempi_rimasti=["Quit"],
                    extra={"lingua_aggiunta": {"nome": "italiano", "impostata": True}})
    controlla("GameMaker con lingua aggiunta: la finestra lo dice", "lingua nuova del gioco" in
              esito.finestra(e)["testo"] and "italiano" in esito.finestra(e)["testo"])
    e = esito.Esito("OK", motore="unity-localization", scritto=True, tradotti=3)
    controlla("Unity Localization: finestra con 'lascia selezionata la lingua English'",
              "English" in esito.finestra(e)["testo"] and esito.finestra(e)["titolo"] == "Traduzione installata")
    try:
        esito.Esito("INVENTATO")
        controlla("codice sconosciuto rifiutato", False)
    except ValueError:
        controlla("codice sconosciuto rifiutato", True)
    e = esito.Esito("PARZIALE", esempi_rimasti=["t%d" % i for i in range(50)], rimasti=50)
    controlla("al massimo 20 esempi rimasti; riassunto per il registro", len(e.esempi_rimasti) == 20
              and e.riassunto().startswith("Esito: PARZIALE"))
    # revisione 0.16: rimaste fuori dal gioco solo traduzioni che il gioco non saprebbe mostrare (scartate quando si
    # scrive): "Correggi traduzioni" apre "Non scritte nel gioco" e il testo nomina quell'elenco, non "Rimasti"
    e = esito.Esito("PARZIALE", "Una traduzione non e' stata scritta perche' il gioco non la mostrerebbe bene.",
                    motore="gamemaker", scritto=True, tradotti=24, rimasti=1, esempi_rimasti=["VULTURE TOKEN"],
                    azioni=[], extra={"scartate": 1})
    f = esito.finestra(e)
    controlla("solo traduzioni scartate dal gioco: 'Correggi traduzioni' su 'Non scritte nel gioco', e il testo lo dice",
              esito.filtro_correzioni(e) == "gioco" and ("Correggi traduzioni", "correggi") in f["pulsanti"]
              and "\"Non scritte nel gioco\"" in f["testo"] and "\"Rimasti in originale\"" not in f["testo"])
    e = esito.Esito("PARZIALE", motore="gamemaker", scritto=True, tradotti=20, rimasti=3, esempi_rimasti=["A", "B"],
                    azioni=["usa_locale"], extra={"scartate": 1, "ritentati": 1})
    f = esito.finestra(e, "riapplica", locale_pronto=True, attuale="locale")
    controlla("testi rimasti e traduzioni scartate dal gioco: 'Rimasti in originale', e il testo nomina anche l'altro "
              "elenco; un testo riprovato al singolare", esito.filtro_correzioni(e) == "rimasti"
              and "Non scritte nel gioco" in f["testo"] and "Un testo scartato dai controlli l'ho già riprovato"
              in f["testo"])


def prova_rapporto(base, mono):
    """Crea rapporto: i file utili ci sono, chiave DeepL ed email no (neanche se finite in un registro)."""
    chiave, email = "chiave-segreta-1234567:fx", "giocatore.segreto@example.com"
    with open(progetto.cartella_dati("settings.json"), "w", encoding="utf-8") as f:
        json.dump({"versione": 2, "backend": "deepl", "chiave_deepl": chiave, "email_mymemory": email,
                   "tema": "auto", "cartella": mono}, f)
    with open(progetto.cartella_dati("services.json"), "w", encoding="utf-8") as f:
        json.dump({"versione": 1, "google": {"richieste": []}}, f)
    registri = progetto.cartella_dati("logs")
    os.makedirs(registri, exist_ok=True)
    nome = os.path.splitext(os.path.basename(automatico.trova_profilo(mono)))[0]
    registro = os.path.join(registri, "20260101-120000-%s.log" % nome)
    with open(registro, "w", encoding="utf-8") as f:
        f.write("PatchSmith - Analisi e traduzione\nchiave usata per sbaglio: %s (%s)\nEsito: OK\n"
                % (chiave, email.upper()))
    with open(automatico.trova_profilo(mono), encoding="utf-8") as f:
        problemi_testi = progetto.percorso_problemi(json.load(f))  # i testi rimasti in originale, con il motivo
    os.makedirs(os.path.dirname(problemi_testi), exist_ok=True)
    with open(problemi_testi, "w", encoding="utf-8") as f:
        json.dump({"Quit": {"motivo": "scartata dai controlli (non tradotta)", "dove": "traduzione",
                            "tentativi": 2}}, f)
    # registri del gioco, di BepInEx e di Unity (azienda e prodotto da app.info), crash, traduttore dal vivo
    open(os.path.join(mono, "BepInEx", "LogOutput.log"), "w", encoding="utf-8").write(
        "[Message:   BepInEx] Chainloader startup complete\n")
    open(os.path.join(mono, "Gioco_Data", "app.info"), "w", encoding="utf-8").write("Azienda Finta\nGioco Finto")
    utente = os.environ["USERPROFILE"]
    locallow = os.path.join(utente, "AppData", "LocalLow", "Azienda Finta", "Gioco Finto")
    os.makedirs(locallow)
    open(os.path.join(locallow, "Player.log"), "w", encoding="utf-8").write("Mono path[0] = '%s'\n" % mono)
    open(os.path.join(locallow, "Player-prev.log"), "w", encoding="utf-8").write("NullReferenceException\n")
    crash = os.path.join(os.environ["TEMP"], "Azienda Finta", "Gioco Finto", "Crashes")
    for n, (cartella, testo) in enumerate((("Crash_vecchio", "vecchio"), ("Crash_nuovo", "crash nuovo"))):
        os.makedirs(os.path.join(crash, cartella))
        open(os.path.join(crash, cartella, "Player.log"), "w").write(testo)
        os.utime(os.path.join(crash, cartella), (time.time() - 100 + n * 50,) * 2)
    lavoro = os.path.join(base, "ollama")
    os.makedirs(lavoro)
    open(os.path.join(lavoro, "servizio-xunity.log"), "w", encoding="utf-8").write(
        "x" * (rapporto.MAX_BYTE + 1000) + "\n2026-10-18 ! traduzione interrotta\n")
    vero = ollama.cartella
    ollama.cartella = lambda: lavoro  # mai i registri veri del traduttore locale
    destinazione = os.path.join(base, "Desktop")
    try:
        ultimo = esito.Esito("PARZIALE", "Motivo", motore="unity", scritto=True, rimasti=1, esempi_rimasti=["Quit"])
        zip_ = rapporto.crea(mono, destinazione, esito=ultimo)
        zip2 = rapporto.crea("", destinazione)
    finally:
        ollama.cartella = vero
    controlla("rapporto: uno zip nella cartella scelta", os.path.isfile(zip_) and os.path.dirname(zip_) == destinazione
              and os.path.basename(zip_).startswith("PatchSmith-rapporto-%s-" % nome))
    with zipfile.ZipFile(zip_) as z:
        nomi = set(z.namelist())
        contenuto = {n: z.read(n).decode("utf-8") for n in nomi}
    attesi = {"riepilogo.txt", "settings.json", "services.json", "rilevamento.txt",
              "registri/" + os.path.basename(registro), "profilo/%s.json" % nome, "gioco/BepInEx/LogOutput.log",
              "gioco/" + adapter_unity.CONFIG_XUNITY, "gioco/" + adapter_unity.CONFIG_BEPINEX,
              "gioco/patchsmith-unity.json", "traduttore-locale/servizio-xunity.log", "unity/Player.log",
              "unity/Player-prev.log", "unity/crash-Crash_nuovo-Player.log",
              "traduzioni/" + os.path.basename(problemi_testi)}
    controlla("rapporto: registro, profilo, impostazioni, stato, rilevamento, BepInEx/XUnity, Unity, crash, "
              "manifesto, testi rimasti con il motivo (problemi.json)", attesi <= nomi)
    tutto = "\n".join(contenuto.values())
    controlla("rapporto: nessuna chiave DeepL ne' email (nemmeno dentro un registro, anche in maiuscolo)",
              chiave not in tutto and email not in tutto.lower() and "<tolto>" in contenuto["registri/" +
                                                                                       os.path.basename(registro)])
    imp = json.loads(contenuto["settings.json"])
    controlla("rapporto: impostazioni senza chiave_deepl ed email, il resto c'e'",
              "chiave_deepl" not in imp and "email_mymemory" not in imp and imp.get("backend") == "deepl")
    controlla("rapporto: versione e ultimo esito nel riepilogo, crash piu' recente, fine del registro lungo",
              __version__ in contenuto["riepilogo.txt"] and "PARZIALE" in contenuto["riepilogo.txt"]
              and contenuto["unity/crash-Crash_nuovo-Player.log"] == "crash nuovo"
              and "traduzione interrotta" in contenuto["traduttore-locale/servizio-xunity.log"]
              and len(contenuto["traduttore-locale/servizio-xunity.log"]) < rapporto.MAX_BYTE + 200)
    controlla("rapporto: la cartella dell'utente diventa %USERPROFILE% (anche dentro un JSON)",
              rapporto.pulisci("log: %s\\AppData" % utente, []) == "log: %USERPROFILE%\\AppData"
              and rapporto.pulisci(json.dumps({"c": utente}), []) == '{"c": "%USERPROFILE%"}'
              and utente.lower() not in tutto.lower())
    controlla("rapporto anche senza cartella del gioco (solo dati generali)", os.path.isfile(zip2))
    pulito = rapporto.pulisci("GET https://api.mymemory.translated.net/get?q=Start&de=%s&x=%s" % (
        email.replace("@", "%40"), email.replace("@", "%40").upper()), [email, chiave])
    controlla("rapporto: l'email sparisce anche scritta come in un indirizzo web (%40)",
              "segreto" not in pulito.lower() and pulito.count("<tolto>") == 2)


def prova_dal_vivo(mono):
    """Chi riceve le frasi nuove mentre si gioca (Unity): Google, il traduttore di PatchSmith (comando del plugin di
    PatchSmith) o nessuno (XUnity senza un traduttore che risponda)."""
    config = os.path.join(mono, *adapter_unity.CONFIG_XUNITY.split("/"))
    servizio = os.path.join(mono, *adapter_unity.CONFIG_SERVIZIO.split("/"))
    plugin = os.path.join(mono, *adapter_unity.PLUGIN_SERVIZIO.split("/"))
    with open(config, encoding="utf-8-sig") as f:
        originale = f.read()
    risultati = [adapter_unity.traduttore_dal_vivo(mono)]
    os.makedirs(os.path.dirname(plugin), exist_ok=True)
    open(plugin, "wb").write(b"finto")
    with open(servizio, "w", encoding="utf-8") as f:
        f.write('Argomenti=-I "smith.py" servizio-xunity --profilo "prova.json" --porta 51234\n')
    risultati.append(adapter_unity.traduttore_dal_vivo(mono))
    os.remove(plugin)
    with open(config, "w", encoding="utf-8") as f:
        f.write(originale.replace("Endpoint=GoogleTranslateV2", "Endpoint=CustomTranslate"))
    risultati.append(adapter_unity.traduttore_dal_vivo(mono))
    with open(config, "w", encoding="utf-8") as f:
        f.write(originale)
    os.remove(servizio)
    risultati.append(adapter_unity.traduttore_dal_vivo(os.path.dirname(mono)))  # cartella senza XUnity
    e = esito.Esito("OK", motore="unity", scritto=True, tradotti=5, dal_vivo="locale")
    controlla("frasi nuove mentre giochi: Google, traduttore di PatchSmith, nessuno (e None senza XUnity)%s" % (
        "" if risultati == ["google", "servizio", "nessuno", None] else ": %s" % risultati),
              risultati == ["google", "servizio", "nessuno", None]
              and "Frasi nuove mentre giochi: PatchSmith, con il traduttore locale (sul tuo PC)."
              in esito.finestra(e)["testo"])
    e = esito.Esito("OK", motore="unity", scritto=True, tradotti=5, dal_vivo="nessuno", azioni=["installa_locale"],
                    extra={"dal_vivo_nota": "senza_traduttore"})
    f = esito.finestra(e, locale_pronto=False, attuale="google")
    controlla("frasi nuove senza traduttore: la finestra lo spiega e propone 'Installa il traduttore locale'",
              "Frasi nuove mentre giochi: nessuno" in f["testo"]
              and esito.NOTE_DAL_VIVO["senza_traduttore"] in f["testo"]
              and ("Installa il traduttore locale", "installa_locale") in f["pulsanti"])


def prova_riga_di_comando(base):
    """I messaggi parlano dei pulsanti dell'interfaccia: il comando per rimediare lo scrive solo la riga di comando
    (sotto l'errore, e in 'smith servizi' per un traduttore disattivato)."""
    from patchsmith import cli
    vero = automatico.analizza_e_traduci

    def senza_locale(*_a, **_k):
        raise backend.ErroreBackend(backend.NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=backend.COMANDO_INSTALLA)

    automatico.analizza_e_traduci = senza_locale
    uscita = io.StringIO()
    try:
        with contextlib.redirect_stdout(uscita):
            codice = cli.main(["auto", base, "--backend", "finto"])
    finally:
        automatico.analizza_e_traduci = vero
    testo = uscita.getvalue()
    controlla("riga di comando: l'errore e sotto il comando per rimediare (codice di uscita 1)",
              codice == 1 and "ERRORE: Il traduttore locale non è installato" in testo
              and "Da riga di comando: python -I smith.py locale --installa" in testo)
    stato = progetto.cartella_dati("services.json")
    adesso = time.time()
    with open(stato, "w", encoding="utf-8") as f:
        json.dump({"versione": 1, "google": {"richieste": [], "blocco": {
            "dal": adesso - 60, "fino": adesso + 86400, "livello": 3, "tipo": "a_freddo", "http": 429,
            "dettaglio": None, "disattivato": True}}}, f)
    uscita = io.StringIO()
    try:
        with contextlib.redirect_stdout(uscita):
            codice = cli.main(["servizi"])
    finally:
        os.remove(stato)
    testo = uscita.getvalue()
    controlla("'smith servizi': per Google disattivato il comando per riattivarlo (e il pulsante nel messaggio)",
              codice == 0 and "python -I smith.py servizi --azzera google" in testo and "Riattiva" in testo
              and "--azzera deepl" not in testo)


def prova_profili_rovinati(base):
    """Un file in profili/ che non e' un profilo (es. una lista) si salta come uno illeggibile: non blocca la
    ricerca del profilo (ne' l'interfaccia, che la fa prima di ogni operazione)."""
    rotto = os.path.join(automatico.cartella_profili(), "rotto.json")
    with open(rotto, "w", encoding="utf-8") as f:
        json.dump(["non", "un", "profilo"], f)
    try:
        nomi = [os.path.basename(p) for p, _ in automatico.elenco_profili()]
        trovato = automatico.trova_profilo(os.path.join(base, "web", "Lantern Keeper"))
        controlla("profilo rovinato (una lista): saltato, gli altri si trovano ancora",
                  "rotto.json" not in nomi and nomi and trovato)
    except Exception as e:  # noqa: BLE001
        controlla("profilo rovinato (una lista): saltato (%s: %s)" % (type(e).__name__, e), False)
    finally:
        os.remove(rotto)


def prova_testi_interfaccia():
    """Nessun testo che arriva all'interfaccia rimanda alla riga di comando: 'smith.py' solo in cli.py, nel percorso
    del servizio per XUnity e nei comandi riservati alla riga di comando (attributo 'comando')."""
    cartella = os.path.join(os.path.dirname(QUI), "patchsmith")
    trovati = []
    for nome in sorted(os.listdir(cartella)):
        if not nome.endswith(".py") or nome == "cli.py":
            continue
        with open(os.path.join(cartella, nome), "rb") as f:
            sorgente = f.read()
        righe = sorgente.decode("utf-8").splitlines()
        for t in tokenize.tokenize(io.BytesIO(sorgente).readline):
            if t.type != tokenize.STRING or ("smith.py" not in t.string and "python -I" not in t.string):
                continue
            riga = righe[t.start[0] - 1]
            if t.string.strip("\"'") == "smith.py" or "comando" in riga.lower():
                continue
            if t.start[0] > 1 and righe[t.start[0] - 2].strip().startswith(("def ", "class ")):
                continue  # docstring
            trovati.append("%s:%d" % (nome, t.start[0]))
    controlla("testi per l'interfaccia senza 'smith.py' (fuori da cli.py)%s" % (
        ": " + ", ".join(trovati) if trovati else ""), not trovati)
    from patchsmith import limitatore
    lim = limitatore.Limitatore("google", log=None, percorso=os.path.join(progetto.cartella_dati(), "stato-prova.json"),
                                ora=lambda: 1_000_000.0)
    for _ in range(4):
        messaggio = lim.bloccato(429)
    controlla("limitatore: messaggi di blocco e disattivazione con il pulsante 'Riattiva', senza comandi",
              "Riattiva" in messaggio and "Riattiva" in lim.pausa() and senza_smith(messaggio + lim.pausa()))


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    # dati di PatchSmith, cartella dell'utente e %TEMP% nella cartella della prova (anche lanciata da sola)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    automatico.CARTELLA_PROFILI = None
    for d in ("profiles", "memory"):
        os.makedirs(os.path.join(progetto.RADICE_DATI, d))
    for variabile, rel in (("USERPROFILE", "utente"), ("TEMP", "utente/Temp"), ("TMP", "utente/Temp"),
                           ("LOCALAPPDATA", "utente/AppData/Local"), ("APPDATA", "utente/AppData/Roaming")):
        os.environ[variabile] = os.path.join(base, *rel.split("/"))
        os.makedirs(os.environ[variabile], exist_ok=True)
    for v in ("PATCHSMITH_DEEPL", "PATCHSMITH_EMAIL"):
        os.environ.pop(v, None)
    rapporto.cartella_desktop = lambda: os.path.join(base, "Desktop")  # mai il Desktop vero
    gioco = prova_web(base)
    prova_google_bloccato(base, gioco)
    prova_gamemaker_aperto(base)
    mono = prova_giochi_unity(base)
    prova_eccezioni()
    prova_finestre()
    prova_rapporto(base, mono)
    prova_dal_vivo(mono)
    prova_profili_rovinati(base)
    prova_riga_di_comando(os.path.join(base, "web", "Lantern Keeper"))
    prova_testi_interfaccia()
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
