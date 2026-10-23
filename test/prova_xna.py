"""Test dell'adapter XNA/.NET con un gioco finto costruito qui (csc.exe di Windows + un font XNB finto).

Uso: python -I test/prova_xna.py <cartella_temporanea_vuota>
Il "gioco" e la sonda sono programmi .NET minimi creati dal test: nessun gioco vero viene avviato.
"""
import hashlib
import json
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_xna, dotnet  # noqa: E402

ESITI = []
SENZA_FINESTRA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def csc(*argomenti, cwd):
    esito = subprocess.run([adapter_xna.compilatore("v4.0.30319"), "/nologo"] + list(argomenti), cwd=cwd,
                           capture_output=True, text=True, encoding="oem", errors="replace",
                           creationflags=SENZA_FINESTRA)
    if esito.returncode:
        raise SystemExit("csc: " + esito.stdout + esito.stderr)


def resources(voci):
    """.resources con sole stringhe, nel formato di ResourceWriter (scritto con l'info minima)."""
    info = {"lettore": dotnet.LETTORE_PREDEFINITO, "insieme": dotnet.INSIEME_PREDEFINITO, "versione": 2, "tipi": [],
            "voci": [(k, dotnet.TIPO_STRINGA, v) for k, v in sorted(voci.items())]}
    return dotnet.scrivi_resources(info)


def font_xnb(caratteri):
    """SpriteFont XNB v5 non compresso con i caratteri dati (texture 1x1, glifi finti)."""
    def s7(n):
        b = bytearray()
        while True:
            x, n = n & 0x7F, n >> 7
            b.append(x | (0x80 if n else 0))
            if not n:
                return bytes(b)

    def stringa(t):
        return s7(len(t.encode())) + t.encode()

    lettori = ["Microsoft.Xna.Framework.Content.SpriteFontReader", "Microsoft.Xna.Framework.Content.Texture2DReader",
               "Microsoft.Xna.Framework.Content.ListReader`1[[Microsoft.Xna.Framework.Rectangle]]",
               "Microsoft.Xna.Framework.Content.ListReader`1[[System.Char]]",
               "Microsoft.Xna.Framework.Content.ListReader`1[[Microsoft.Xna.Framework.Vector3]]"]
    corpo = s7(len(lettori)) + b"".join(stringa(l) + struct.pack("<i", 0) for l in lettori) + s7(0)
    corpo += s7(1) + s7(2) + struct.pack("<iIII", 0, 1, 1, 1) + struct.pack("<I", 4) + b"\xff" * 4
    n = len(caratteri)
    for _ in range(2):
        corpo += s7(3) + struct.pack("<I", n) + struct.pack("<iiii", 0, 0, 1, 1) * n
    corpo += s7(4) + struct.pack("<I", n) + "".join(caratteri).encode("utf-8")
    corpo += struct.pack("<if", 12, 0.0) + s7(5) + struct.pack("<I", n) + struct.pack("<fff", 0, 1, 0) * n + b"\x00"
    return b"XNBw\x05\x00" + struct.pack("<I", 10 + len(corpo)) + corpo


TESTI_EN = {"Menu_Start": "Start the adventure", "Menu_Quit": "Quit game", "Tip_1": "Press [Input:12] to jump\\nfast",
            "Count": "{0} heroes", "Only_Number": "100", "Lang_En": "English"}


def crea_gioco(base, lavoro):
    os.makedirs(os.path.join(base, "de"))
    os.makedirs(os.path.join(base, "Content", "Fonts"))
    neutre = os.path.join(lavoro, "neutre.resources")
    open(neutre, "wb").write(resources(TESTI_EN))
    open(os.path.join(lavoro, "Gioco.cs"), "w").write(
        "[assembly: System.Reflection.AssemblyVersion(\"1.2.0.0\")]\n"
        "class Gioco { static void Main() {} }\n")
    csc("/target:exe", "/platform:x86", "/out:" + os.path.join(base, "Gioco.exe"),
        "/resource:%s,Gioco.Testi.resources" % neutre, "Gioco.cs", cwd=lavoro)
    tedesche = os.path.join(lavoro, "de.resources")
    open(tedesche, "wb").write(resources({"Menu_Start": "Das Abenteuer beginnen"}))
    open(os.path.join(lavoro, "Sat.cs"), "w").write(
        "[assembly: System.Reflection.AssemblyCulture(\"de\")]\n[assembly: System.Reflection.AssemblyVersion(\"1.2.0.0\")]\n")
    csc("/target:library", "/out:" + os.path.join(base, "de", "Gioco.resources.dll"),
        "/resource:%s,Gioco.Testi.de.resources" % tedesche, "Sat.cs", cwd=lavoro)
    ascii_ = [chr(c) for c in range(32, 127)]
    open(os.path.join(base, "Content", "Fonts", "Pixel.xnb"), "wb").write(font_xnb(ascii_))
    open(os.path.join(base, "Content", "Fonts", "Numeri.xnb"), "wb").write(font_xnb(list("0123456789")))
    # sonda: usa il vero ResourceManager di .NET sulla copia del gioco
    open(os.path.join(lavoro, "Sonda.cs"), "w").write(
        "using System; using System.Globalization; using System.Reflection; using System.Resources;\n"
        "class Sonda { static void Main(string[] a) {\n"
        "  Console.OutputEncoding = System.Text.Encoding.UTF8;\n"
        "  var rm = new ResourceManager(\"Gioco.Testi\", Assembly.LoadFrom(a[0]));\n"
        "  foreach (var c in new[] {\"en-US\", \"it-IT\", \"de-DE\", \"fr-FR\"})\n"
        "    Console.WriteLine(c + \"=\" + rm.GetString(\"Menu_Start\", new CultureInfo(c)));\n"
        "}}\n")
    os.makedirs(os.path.join(lavoro, "sonda"))
    csc("/target:exe", "/platform:x86", "/out:" + os.path.join(lavoro, "sonda", "Sonda.exe"), "Sonda.cs", cwd=lavoro)


def sonda(lavoro, gioco):
    esito = subprocess.run([os.path.join(lavoro, "sonda", "Sonda.exe"), os.path.join(gioco, "Gioco.exe")],
                           capture_output=True, text=True, encoding="utf-8", creationflags=SENZA_FINESTRA)
    return dict(r.split("=", 1) for r in esito.stdout.splitlines() if "=" in r)


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
    adapter_xna.gioco_aperto = lambda c: False
    gioco, lavoro = os.path.join(base, "gioco"), os.path.join(base, "lavoro")
    os.makedirs(lavoro)
    crea_gioco(gioco, lavoro)
    prima = impronte(gioco)

    # versione dei satelliti: [SatelliteContractVersion("1.0")] vale 1.0.0.0, come per csc
    open(os.path.join(lavoro, "Contratto.cs"), "w").write(
        "[assembly: System.Resources.SatelliteContractVersion(\"1.0\")]\nclass C {}\n")
    csc("/target:library", "/out:" + os.path.join(lavoro, "Contratto.dll"), "Contratto.cs", cwd=lavoro)
    controlla("SatelliteContractVersion \"1.0\" -> 1.0.0.0",
              dotnet.Assembly(os.path.join(lavoro, "Contratto.dll")).versione_satelliti() == "1.0.0.0")
    # nomi con lo stesso hash: si riscrivono identici e si rileggono tutti
    voci = {"K!!!": "uno", "K!>^": "due", "K\"B!": "tre", "Altro": "quattro"}
    controlla("nomi con lo stesso hash (prova valida)", len({dotnet.hash_nome(k) for k in list(voci)[:3]}) == 1)
    dati = resources(voci)
    letto = dotnet.leggi_resources(dati)
    controlla("nomi con lo stesso hash: riscrittura identica e testi giusti",
              dotnet.scrivi_resources(letto) == dati and dotnet.stringhe_resources(letto) == voci)

    insiemi = adapter_xna.trova_risorse(gioco)
    controlla("trova le risorse localizzate (base Gioco.Testi, satellite de)",
              len(insiemi) == 1 and insiemi[0]["basi"] == {"Gioco.Testi": ["de"]} and insiemi[0]["versione"] == "1.2.0.0")
    _, testi = adapter_xna.testi_da_tradurre(gioco, silenzio)
    controlla("testi da tradurre (senza i numeri)", "Start the adventure" in testi and "100" not in testi)
    ammessi = adapter_xna.caratteri_font(gioco)
    controlla("caratteri dei font: solo ASCII (il font di sole cifre non conta)", ammessi == {chr(c) for c in range(32, 127)})
    controlla("accenti -> apostrofo per l'italiano", adapter_xna.adatta_caratteri("Perché è così", ammessi, "it")
              == "Perche' e' cosi'")
    controlla("accenti tolti per le altre lingue", adapter_xna.adatta_caratteri("Été", ammessi, "fr") == "Ete")
    controlla("carattere impossibile -> scartato", adapter_xna.adatta_caratteri("漢字", ammessi, "it") is None)
    controlla("punteggiatura tipografica -> ASCII", adapter_xna.adatta_caratteri("L’eroe…", ammessi, "it") == "L'eroe...")
    controlla("tab -> spazio, invisibili tolti", adapter_xna.adatta_caratteri("a\tb​c", ammessi, "it") == "a bc")
    finto = {"lingua_neutra": None, "basi": {"X": ["de", "fr"]}}
    controlla("culture: en e it se il gioco non dichiara la lingua neutra",
              adapter_xna.culture_da_creare(finto, "en", "it") == ["en", "it"])
    controlla("culture: en-US se il gioco dichiara la lingua neutra 'en'",
              adapter_xna.culture_da_creare(dict(finto, lingua_neutra="en"), "en", "it") == ["en-US", "it"])
    controlla("culture: niente slot inglese se la lingua neutra e' gia' 'en-US'; niente 'it' se il gioco lo ha",
              adapter_xna.culture_da_creare(dict(finto, lingua_neutra="en-US", basi={"X": ["it"]}), "en", "it") == [])

    memoria = {"Start the adventure": "Inizia l'avventura è qui", "Quit game": "Esci dal gioco",
               "Press [Input:12] to jump\\nfast": "Premi [Input:12] per saltare\\nveloce", "{0} heroes": "{0} eroi"}
    n = adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    from patchsmith import __version__
    controlla("manifesto con la versione di PatchSmith (per il controllo dopo aver giocato)",
              adapter_xna.leggi_manifesto(gioco).get("patchsmith_versione") == __version__)
    controlla("satelliti en e it creati (4 testi tradotti + voce lingua)", n == 5
              and os.path.exists(os.path.join(gioco, "en", "Gioco.resources.dll"))
              and os.path.exists(os.path.join(gioco, "it", "Gioco.resources.dll")))
    sat = dotnet.Assembly(os.path.join(gioco, "en", "Gioco.resources.dll"))
    tradotte = dotnet.stringhe_resources(dotnet.leggi_resources(sat.leggi_risorsa("Gioco.Testi.en.resources")))
    controlla("voce 'English' del menu lingue -> 'Italiano'; segnaposto intatti", tradotte["Lang_En"] == "Italiano"
              and tradotte["Tip_1"] == "Premi [Input:12] per saltare\\nveloce" and tradotte["Only_Number"] == "100")
    risposte = sonda(lavoro, gioco)
    controlla("ResourceManager vero: en-US e it-IT danno l'italiano (accenti adattati)",
              risposte.get("en-US") == "Inizia l'avventura e' qui" and risposte.get("it-IT") == "Inizia l'avventura e' qui")
    controlla("ResourceManager vero: il tedesco resta tedesco, il francese resta inglese",
              risposte.get("de-DE") == "Das Abenteuer beginnen" and risposte.get("fr-FR") == "Start the adventure")
    dopo = impronte(gioco)
    controlla("file originali del gioco non toccati", all(dopo.get(k) == v for k, v in prima.items()))

    righe = []
    adapter_xna.stato(gioco, righe.append)
    controlla("stato: satelliti presenti e integri", sum(r.strip().startswith("OK") for r in righe) == 2)

    # reinstallazione (riapplica): stessi file, nessun errore
    adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    controlla("riapplica: nessun file in piu'", set(impronte(gioco)) == set(dopo))

    adapter_xna.disinstalla(gioco, silenzio)
    controlla("disinstalla: cartella identica a prima", impronte(gioco) == prima
              and not os.path.exists(os.path.join(gioco, "en")))
    controlla("disinstalla: il gioco torna in inglese", sonda(lavoro, gioco).get("en-US") == "Start the adventure")

    # satellite en gia' presente e non nostro: non si sovrascrive
    os.makedirs(os.path.join(gioco, "en"))
    open(os.path.join(gioco, "en", "Gioco.resources.dll"), "wb").write(b"di qualcun altro")
    try:
        adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
        controlla("satellite altrui: non sovrascritto", False)
    except adapter_xna.ErroreXna:
        controlla("satellite altrui: non sovrascritto", open(os.path.join(gioco, "en", "Gioco.resources.dll"), "rb").read()
                  == b"di qualcun altro" and not os.path.exists(os.path.join(gioco, adapter_xna.MANIFESTO)))
    os.remove(os.path.join(gioco, "en", "Gioco.resources.dll"))
    os.rmdir(os.path.join(gioco, "en"))

    # manifesto alterato: nulla cancellato
    sat_de = os.path.join(gioco, "de", "Gioco.resources.dll")
    falso = {"patchsmith": True, "file": {"de/Gioco.resources.dll": hashlib.sha256(open(sat_de, "rb").read()).hexdigest()},
             "cartelle_create": ["de"]}
    open(os.path.join(gioco, adapter_xna.MANIFESTO), "w").write(json.dumps(falso))
    adapter_xna.disinstalla(gioco, silenzio)
    controlla("manifesto che punta al satellite del gioco con l'impronta giusta: intatto (non ha il marcatore)",
              os.path.exists(sat_de) and impronte(gioco) == prima)
    falso = {"patchsmith": True, "file": {"../Gioco.exe": "0" * 64}}
    open(os.path.join(gioco, adapter_xna.MANIFESTO), "w").write(json.dumps(falso))
    try:
        adapter_xna.disinstalla(gioco, silenzio)
        controlla("manifesto con percorso fuori posto: rifiutato", False)
    except adapter_xna.ErroreXna:
        controlla("manifesto con percorso fuori posto: rifiutato", os.path.exists(os.path.join(gioco, "Gioco.exe")))
    os.remove(os.path.join(gioco, adapter_xna.MANIFESTO))

    # installazione interrotta prima di mettere i file al loro posto: il ripristino toglie tutto
    vero_replace = os.replace

    def replace_interrotto(a, b):
        if b.endswith(".resources.dll"):
            raise KeyboardInterrupt
        return vero_replace(a, b)

    adapter_xna.os.replace = replace_interrotto
    try:
        adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    except KeyboardInterrupt:
        pass
    finally:
        adapter_xna.os.replace = vero_replace
    adapter_xna.disinstalla(gioco, silenzio)
    controlla("installazione interrotta: il ripristino toglie tutto", impronte(gioco) == prima)

    # --- scenari emersi dalla revisione ---------------------------------------------------------
    # manifesto perso ma satelliti nostri presenti: non contano come lingue del gioco, stato e ripristino li vedono
    adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    os.remove(os.path.join(gioco, adapter_xna.MANIFESTO))
    controlla("senza manifesto: i satelliti nostri non contano come lingue del gioco",
              adapter_xna.trova_risorse(gioco)[0]["basi"] == {"Gioco.Testi": ["de"]})
    righe = []
    adapter_xna.stato(gioco, righe.append)
    controlla("senza manifesto: lo stato li segnala", any("senza manifesto" in r for r in righe))
    controlla("senza manifesto: il ripristino li toglie", adapter_xna.disinstalla(gioco, silenzio) and impronte(gioco) == prima)

    # sostituzione interrotta, poi cambio lingua, poi ripristino: niente resta indietro
    adapter_xna.os.replace = replace_interrotto
    try:
        adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    except KeyboardInterrupt:
        pass
    finally:
        adapter_xna.os.replace = vero_replace
    adapter_xna.installa(gioco, "en", "es", "prova", {"Start the adventure": "Empezar"}, silenzio)
    controlla("cambio lingua dopo un'interruzione: c'e' solo il satellite es (oltre a en)",
              os.path.exists(os.path.join(gioco, "es", "Gioco.resources.dll"))
              and not os.path.exists(os.path.join(gioco, "it")))
    adapter_xna.disinstalla(gioco, silenzio)
    controlla("... e il ripristino riporta tutto com'era", impronte(gioco) == prima)

    # manifesto che elenca il satellite ufficiale con la sua impronta: una nuova installazione non lo tocca
    falso = {"patchsmith": True, "file": {"de/Gioco.resources.dll": hashlib.sha256(open(sat_de, "rb").read()).hexdigest()},
             "cartelle_create": ["de"]}
    open(os.path.join(gioco, adapter_xna.MANIFESTO), "w").write(json.dumps(falso))
    adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    controlla("installa con manifesto alterato: il satellite ufficiale de resta", os.path.exists(sat_de)
              and impronte(gioco)["de" + os.sep + "Gioco.resources.dll"] == prima["de" + os.sep + "Gioco.resources.dll"])
    adapter_xna.disinstalla(gioco, silenzio)

    # lingua scritta a mano non valida: errore prima di scrivere qualsiasi cosa
    try:
        adapter_xna.installa(gioco, "English", "Italian", "prova", memoria, silenzio)
        controlla("lingua non valida: rifiutata", False)
    except adapter_xna.ErroreXna:
        controlla("lingua non valida: rifiutata senza scrivere nulla", impronte(gioco) == prima)

    # errore di scrittura a meta': nessuna cartella vuota resta indietro
    vero_open = open

    def open_rotto(percorso, modo="r", *a, **k):
        if str(percorso).endswith(adapter_xna.SUFFISSO_TMP) and "w" in modo:
            raise OSError("disco pieno (simulato)")
        return vero_open(percorso, modo, *a, **k)

    adapter_xna.open = open_rotto
    try:
        adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    except OSError:
        pass
    finally:
        del adapter_xna.open
    adapter_xna.disinstalla(gioco, silenzio)
    controlla("errore di scrittura: nessuna cartella vuota rimasta", impronte(gioco) == prima
              and not os.path.exists(os.path.join(gioco, "en")) and not os.path.exists(os.path.join(gioco, "it")))

    # font: cartella con [..] nel nome, XNB compresso, elenco non ordinato, font di sole maiuscole
    fonts = os.path.join(base, "font [v1.0]")
    os.makedirs(os.path.join(fonts, "Content", "Fonts"))
    italiano = sorted(set(chr(c) for c in range(32, 127)) | set("àèéìòù"))
    open(os.path.join(fonts, "Content", "Fonts", "A.xnb"), "wb").write(font_xnb(italiano))
    open(os.path.join(fonts, "Content", "Fonts", "B.xnb"), "wb").write(font_xnb(italiano))
    controlla("font: cartella con [..] nel nome letta, accenti ammessi", "è" in (adapter_xna.caratteri_font(fonts) or ()))
    compresso = bytearray(font_xnb(italiano))
    compresso[5] = 0x80
    open(os.path.join(fonts, "Content", "Fonts", "C.xnb"), "wb").write(bytes(compresso))
    controlla("font: un XNB compresso (illeggibile) -> solo ASCII", adapter_xna.caratteri_font(fonts) == adapter_xna.ASCII)
    os.remove(os.path.join(fonts, "Content", "Fonts", "C.xnb"))
    disordinato = italiano[:-6] + list("ùòìéèà")
    open(os.path.join(fonts, "Content", "Fonts", "B.xnb"), "wb").write(font_xnb(disordinato))
    trovati = adapter_xna.caratteri_font(fonts)
    controlla("font: elenco non ordinato -> solo i caratteri che XNA trova davvero",
              "a" in trovati and not set("àèéìòù") <= trovati)
    maiuscole = [chr(c) for c in range(32, 97)] + list("{|}~")
    open(os.path.join(fonts, "Content", "Fonts", "B.xnb"), "wb").write(font_xnb(maiuscole))
    trovati = adapter_xna.caratteri_font(fonts)
    controlla("font di sole maiuscole: vale anche per le minuscole, non per gli accenti",
              "a" in trovati and "A" in trovati and "è" not in trovati)

    # aggiornamento del gioco: lo stato lo segnala
    adapter_xna.installa(gioco, "en", "it", "prova", memoria, silenzio)
    open(os.path.join(gioco, "Gioco.exe"), "ab").write(b"\x00")
    righe = []
    adapter_xna.stato(gioco, righe.append)
    controlla("gioco aggiornato: segnalato", any("aggiornato" in r for r in righe))
    adapter_xna.disinstalla(gioco, silenzio)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
