"""Lingua dell'interfaccia (interfaccia.py, patchsmith/interfaccia/*.json).

Uso: python -I test/prova_interfaccia.py <cartella_di_prova_che_non_esiste>
- traduzione dei testi: uguali, con valori dentro (%s, %d), pezzi tradotti dentro, prefissi del registro, piu' righe;
- il file inglese: ogni traduzione ha gli stessi segnaposto del testo italiano;
- la finestra in inglese: pulsanti, menu a tendina, lingue "Da"/"A" (e ritorno al codice), bandierina con il menu delle
  lingue; scelta una lingua, tutta la finestra (registro compreso) cambia subito, senza riaprire, e la ricorda.
"""
import collections
import json
import os
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import gui, icona, interfaccia, lingua, progetto  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def prova_traduzione():
    interfaccia.imposta("en")
    t = interfaccia.traduci
    controlla("testi uguali, con valori dentro, pezzi dentro, prefissi del registro, ripieghi e righe non nel file",
              t("Crea rapporto") == "Create report"
              and t("Tradotti 400 di 1654 (24%)...") == "Translated 400 of 1654 (24%)..."
              and t("  ! Il gioco e' aperto: chiudilo e riprova.") == "  ! The game is open: close it and try again."
              and t("Analisi e traduzione: D:\\giochi\\x") == "Analysis and translation: D:\\giochi\\x"
              and t("Inglese (en)") == "English (en)"
              and t("Una frase che non c'e' nel file") == "Una frase che non c'e' nel file"
              and t("Crea rapporto\nCrea rapporto") == "Create report\nCreate report")
    interfaccia.imposta("it")
    controlla("in italiano nessuna traduzione", interfaccia.traduci("Crea rapporto") == "Crea rapporto")
    lingue = interfaccia.lingue()
    controlla("lingue disponibili: italiano (dal codice) e inglese (dal file)",
              lingue["it"]["nome"] == "Italiano" and lingue["en"]["nome"] == "English")


def prova_file():
    with open(os.path.join(interfaccia.CARTELLA, "en.json"), encoding="utf-8") as f:
        testi = json.load(f)["testi"]
    diversi = [k for k, v in testi.items() if collections.Counter(interfaccia._SEGNO.findall(k))
               != collections.Counter(interfaccia._SEGNO.findall(v))]
    controlla("file inglese: %d testi, ogni traduzione con gli stessi segnaposto (%s)" % (len(testi), diversi[:3]),
              len(testi) > 1000 and not diversi)


def _pixel_png(percorso):
    """Le righe di pixel RGBA di un PNG scritto da icona.png (senza filtri), per confrontare i pixel e non i byte
    compressi: zlib e zlib-ng (Python 3.14+) comprimono in modo diverso."""
    import struct
    import zlib
    with open(percorso, "rb") as f:
        dati = f.read()
    lato, posizione, compresso = struct.unpack(">I", dati[16:20])[0], 8, b""
    while posizione < len(dati):
        lunghezza, tipo = struct.unpack(">I4s", dati[posizione:posizione + 8])
        if tipo == b"IDAT":
            compresso += dati[posizione + 8:posizione + 8 + lunghezza]
        posizione += 12 + lunghezza
    grezzo = zlib.decompress(compresso)
    riga = 1 + 4 * lato
    return [grezzo[i * riga + 1:(i + 1) * riga] for i in range(lato)]


def prova_suoni(app, radice):
    """Suoni: spenti di default, il pulsante li accende (e li ricorda); alla fine di un'operazione la prima finestra
    suona una volta sola il suono del suo tipo; il controllo all'apertura no; tutti di suoni.DURATA secondi."""
    import wave
    import io
    from patchsmith import suoni, tema
    sentiti, vero = [], suoni.suona
    suoni.suona = sentiti.append
    try:
        spenti = not app.suoni_accesi
        app._cambia_suoni()
        with open(gui._impostazioni(), encoding="utf-8") as f:
            ricordati = json.load(f).get("suoni")
        acceso = sentiti == ["clic"]
        del sentiti[:]

        def finta_operazione(*_a):  # come _fine_operazione: due finestre una dopo l'altra
            tema.AL_MOSTRARE("errore")
            tema.AL_MOSTRARE("info")
        reale = app._fine_operazione
        app._fine_operazione = finta_operazione
        app._fine("Analisi e traduzione", None, None)
        dopo_errore = list(sentiti)
        app._fine_operazione = lambda *_a: tema.AL_MOSTRARE("domanda")
        app._fine("Analisi e traduzione", None, None)
        app._fine_operazione = lambda *_a: tema.AL_MOSTRARE("avviso")  # un avviso: come una domanda
        app._fine("Analisi e traduzione", None, None)

        def in_parte(*_a):  # tradotto in parte (avviso), come in _mostra_finestra: "fatto"
            app._suono_forzato = "fatto"
            tema.AL_MOSTRARE("avviso")
        app._fine_operazione = in_parte
        app._fine("Analisi e traduzione", None, None)
        app._operazione, app._controllo_automatico = "controlla", True
        app._fine("Controllo", None, None)  # all'apertura niente "fatto" (anche tradotto in parte): l'avviso suona
        app._operazione, app._controllo_automatico = None, False
        app._fine_operazione = reale
        tema.AL_MOSTRARE("info")  # fuori dalla fine di un'operazione ogni finestra suona: informazioni e avvisi
        tema.AL_MOSTRARE("avviso")  # come le domande (es. l'avviso di Google quando lo scegli), errori come errori
        tema.AL_MOSTRARE("errore")
        radice.after(150, lambda: [w.destroy() for w in radice.winfo_children() if isinstance(w, tk.Toplevel)])
        app._suono_atteso = True
        tema.informa(radice, app.colori, "Prova", "Una finestra vera: chiama AL_MOSTRARE")
        # il clic dei pulsanti: uno premuto suona, uno disattivato no (anche in un'altra finestra)
        eventi = list(sentiti)
        del sentiti[:]
        altra = tk.Toplevel(radice)
        pulsante, spento = gui.ttk.Button(altra, text="Prova"), gui.ttk.Button(altra, text="Spento")
        pulsante.pack()
        spento.pack()
        spento.state(["disabled"])
        radice.update()
        pulsante.event_generate("<ButtonPress-1>", x=5, y=5)
        spento.event_generate("<ButtonPress-1>", x=5, y=5)
        clic_pulsanti = list(sentiti)
        altra.destroy()
        app._cambia_suoni()  # di nuovo spenti
        pulsante_muto = gui.ttk.Button(radice, text="Muto")
        pulsante_muto.place(x=0, y=0)
        radice.update()
        del sentiti[:]
        pulsante_muto.event_generate("<ButtonPress-1>", x=5, y=5)  # suoni spenti: niente
        clic_pulsanti.append(list(sentiti))
        pulsante_muto.destroy()
        with open(gui._impostazioni(), encoding="utf-8") as f:
            spenti_dopo = json.load(f).get("suoni") is False and not app.suoni_accesi
    finally:
        suoni.suona = vero
    durate = [wave.open(io.BytesIO(suoni.SUONI[n]())).getnframes() / suoni.FREQUENZA for n in ("fatto", "errore",
                                                                                                    "domanda")]
    clic = wave.open(io.BytesIO(suoni.clic())).getnframes() / suoni.FREQUENZA
    controlla("suoni: spenti di default, accesi (con il clic) e ricordati dal pulsante; alla fine di un'operazione "
              "la prima finestra suona \"fatto\" se e' un'informazione o un tradotto in parte (non al controllo "
              "all'apertura); ogni altra finestra sempre, errori come errori e il resto come domande (%s); durata %s"
              % (eventi, durate),
              spenti and ricordati is True and acceso and dopo_errore == ["errore", "domanda"]
              and eventi == ["errore", "domanda", "domanda", "domanda", "fatto", "domanda", "domanda", "domanda",
                              "errore", "fatto"]
              and spenti_dopo
              and all(abs(d - suoni.DURATA) < 0.001 for d in durate) and clic < 0.1)
    controlla("clic dei pulsanti (anche in un'altra finestra), non per quelli disattivati ne' a suoni spenti (%s)"
              % clic_pulsanti, clic_pulsanti == ["clic", []])


def prova_musica(app):
    """Musica di sottofondo: spenta di default, il pulsante con la nota la accende e la spegne (e lo ricorda); il loop
    lo fa Windows senza stacchi (provato in silenzio); "Miniera" di 16 battute giuste."""
    import inspect
    import re
    import time
    from patchsmith import musica
    chiamate, veri = [], (musica.avvia, musica.ferma)
    musica.avvia, musica.ferma = (lambda *_a: chiamate.append("avvia")), (lambda: chiamate.append("ferma"))
    try:
        spenta = not app.musica_accesa
        app._cambia_musica()
        with open(gui._impostazioni(), encoding="utf-8") as f:
            ricordata = json.load(f).get("musica")
        app._cambia_musica()
        with open(gui._impostazioni(), encoding="utf-8") as f:
            spenta_dopo = json.load(f).get("musica") is False
    finally:
        musica.avvia, musica.ferma = veri
    silenzio = b"\0\0" * int(0.2 * musica.FREQUENZA)  # 0,2 secondi di silenzio, ripetuti dal loop di Windows
    aperta = musica._apri(silenzio)
    time.sleep(0.7)
    suonati = musica._suonati()
    musica._chiudi()
    # pronta in anticipo: la prima volta generata e scritta in un file (qui in una cartella temporanea di prova), poi
    # letta dal file in un attimo
    import tempfile
    vecchia, tempfile.tempdir = tempfile.tempdir, tempfile.mkdtemp()
    try:
        musica._PRONTI.clear()
        generata = musica.prepara()
        scritta = os.path.isfile(musica._file_pronto("miniera"))
        musica._PRONTI.clear()
        inizio = time.time()
        letta = musica.prepara()
        secondi = time.time() - inizio
    finally:
        tempfile.tempdir = vecchia
    melodia = re.search(r'melodia = """(.*?)"""', inspect.getsource(musica.miniera), re.S).group(1)
    battute = [sum(p for _n, p in musica._leggi(b)) for b in re.split(r"\s{3,}", melodia.strip())]
    controlla("musica: spenta di default, accesa e spenta dal pulsante con la nota e ricordata (%s); loop senza stacchi "
              "(%s byte suonati da un blocco di %d); Miniera di %d battute da 8 ottavi" % (chiamate, suonati,
                                                                                         len(silenzio), len(battute)),
              spenta and ricordata is True and spenta_dopo and chiamate == ["avvia", "ferma"]
              and (not aperta or suonati and suonati > 2 * len(silenzio)) and musica._stato["uscita"] is None
              and battute == [8] * 16)
    controlla("musica pronta in anticipo: generata e salvata la prima volta, poi letta dal file (%.3f s)" % secondi,
              generata and scritta and letta == generata and secondi < 0.5)


def prova_finestra(base):
    progetto.RADICE_DATI = os.path.join(base, "dati")
    gui.IMPOSTAZIONI = None
    os.makedirs(progetto.RADICE_DATI, exist_ok=True)
    with open(gui._impostazioni(), "w", encoding="utf-8") as f:
        json.dump({"interfaccia": "en", "da": "auto", "a": "it", "backend": "google"}, f)
    radice = tk.Tk()
    try:
        app = gui.App(radice)
        pulsanti = [str(w.cget("text")) for w in app.pulsanti if isinstance(w, gui.ttk.Button)]
        controlla("finestra in inglese: pulsanti (%s), traduttore, lingue Da/A e ritorno al codice" % pulsanti[:4],
                  "Analyze and translate" in pulsanti and "Create report" in pulsanti
                  and app.traduttore.get() == "Free Google (unofficial)" and app._scelto == "google"
                  and app.da.get() == "Detect automatically" and app._codice_lingua(app.da.get()) == lingua.AUTO
                  and app._codice_lingua("English (en)") == "en" and app._lingua_a() == "it")
        radice.update()
        altezze = (app.btn_sfoglia.winfo_height(), app.voce_cartella.winfo_height())
        posizioni = (app.btn_sfoglia.winfo_rooty(), app.voce_cartella.winfo_rooty())
        controlla("'Browse…' alto quanto il campo della cartella e alla stessa altezza (%s, %s)" % (altezze, posizioni),
                  altezze[0] == altezze[1] and posizioni[0] == posizioni[1])
        uguali = all(_pixel_png(os.path.join(icona.CARTELLA, "%d.png" % lato)) == icona.disegna(lato)
                     for lato in (16, 32))
        controlla("icona (martello e cartella) sulla finestra e per quelle che si aprono dopo; le immagini sono quelle che "
                  "disegna icona.py", len(getattr(radice, "_icona_patchsmith", ())) == 6 and uguali)
        app._menu_lingue()
        voci = [str(v.cget("text")).strip() for _c, v in app._voci_lingue]
        aperta = app._tendina_lingue is not None
        app._menu_lingue()  # di nuovo: si chiude
        controlla("bandierina con la tendina delle lingue (%s), nomi nella loro lingua; si apre e si chiude" % voci,
                  "Italiano (it)" in voci and "English (en)" in voci and app.btn_lingua.cget("image") and aperta
                  and app._tendina_lingue is None)
        prima = app.colori["nome"]
        simbolo_prima = str(app.btn_tema.cget("text"))
        app._cambia_tema()
        dopo, simbolo_dopo = app.colori["nome"], str(app.btn_tema.cget("text"))
        with open(gui._impostazioni(), encoding="utf-8") as f:
            tema_salvato = json.load(f).get("tema")
        app._cambia_tema()
        controlla("pulsante sole/luna: tema scuro <-> chiaro subito (anche il registro), simbolo giusto, ricordato",
                  {prima, dopo} == {"scuro", "chiaro"} and tema_salvato == dopo and app.colori["nome"] == prima
                  and simbolo_prima == ("☾" if prima == "scuro" else "☀") and simbolo_dopo != simbolo_prima
                  and str(app.testo.cget("background")) == app.colori["campo"])
        prova_suoni(app, radice)
        prova_musica(app)
        app._log("Analisi e traduzione: D:\\giochi\\x")
        app.stato.set("Tradotti 400 di 1654 (24%)...")
        app._svuota_coda()
        registro_en = app.testo.get("1.0", "end").strip()
        partiti = []
        vero = gui.subprocess.Popen
        gui.subprocess.Popen = lambda *a, **k: partiti.append(a[0])
        try:
            app._cambia_interfaccia("it")
            radice.update()
            pulsanti = [str(w.cget("text")) for w in app.pulsanti if isinstance(w, gui.ttk.Button)]
            with open(gui._impostazioni(), encoding="utf-8") as f:
                salvata = json.load(f).get("interfaccia")
            controlla("scelta l'italiano: tutta la finestra subito in italiano, senza riaprire PatchSmith (pulsanti %s, "
                      "traduttore, Da/A con le stesse scelte, registro, riga di stato) e ricordato" % pulsanti[:2],
                      not partiti and radice.winfo_exists() and salvata == "it"
                      and "Analizza e traduci" in pulsanti and "Crea rapporto" in pulsanti
                      and app.traduttore.get() == "Google gratuito (non ufficiale)" and app._scelto == "google"
                      and "Google gratuito (non ufficiale)" in app.scelta_traduttore.cget("values")
                      and app.da.get() == "Rileva automaticamente" and app._codice_lingua(app.da.get()) == lingua.AUTO
                      and app._lingua_a() == "it" and app.a.get() == "Italiano (it)"
                      and registro_en == "Analysis and translation: D:\\giochi\\x"
                      and app.testo.get("1.0", "end").strip() == "Analisi e traduzione: D:\\giochi\\x"
                      and app.stato.get() == "Tradotti 400 di 1654 (24%)...")
            app._cambia_interfaccia("en")
            radice.update()
            pulsanti = [str(w.cget("text")) for w in app.pulsanti if isinstance(w, gui.ttk.Button)]
            controlla("e di nuovo l'inglese: pulsanti e registro rifatti",
                      not partiti and "Analyze and translate" in pulsanti and app.da.get() == "Detect automatically"
                      and app.testo.get("1.0", "end").strip() == registro_en
                      and app.stato.get() == "Translated 400 of 1654 (24%)...")
        finally:
            gui.subprocess.Popen = vero
    finally:
        try:
            radice.destroy()
        except tk.TclError:
            pass
        interfaccia.imposta("it")
        gui._traduci_costanti()


def prova_eseguibile(base):
    """PatchSmith.exe con l'icona accanto a un PatchSmith.pyw di prova: compilato, non rifatto se e' gia' aggiornato,
    e al doppio clic apre il .pyw con il Python installato (qui il .pyw di prova scrive solo un file)."""
    if not sys.platform.startswith("win"):
        return
    cartella = os.path.join(base, "programma con spazi")
    os.makedirs(cartella)
    with open(os.path.join(cartella, "PatchSmith.pyw"), "w", encoding="utf-8") as f:
        f.write("import os, sys\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'partito.txt'), 'w')"
                ".write(sys.version)\n")
    creato = icona.eseguibile(cartella)
    chiamate, vero = [], icona.subprocess.run
    icona.subprocess.run = lambda *a, **k: chiamate.append(a)
    try:
        di_nuovo = icona.eseguibile(cartella)
    finally:
        icona.subprocess.run = vero
    exe = os.path.join(cartella, "PatchSmith.exe")
    with open(exe, "rb") as f:
        dati = f.read()
    partito = os.path.join(cartella, "partito.txt")
    if creato:
        subprocess.run([exe], timeout=30)
        for _ in range(40):
            if os.path.isfile(partito):
                break
            time.sleep(0.25)
    controlla("PatchSmith.exe con l'icona dentro accanto al .pyw (%d byte); non ricompilato se aggiornato; apre il .pyw "
              "con Python" % len(dati), creato and di_nuovo and not chiamate and dati[:2] == b"MZ"
              and b"\x89PNG" in dati and os.path.isfile(partito))


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    prova_traduzione()
    prova_file()
    prova_finestra(base)
    prova_eseguibile(base)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return 1 if falliti else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
