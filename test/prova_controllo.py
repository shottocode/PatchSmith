"""Prove del controllo dopo aver giocato (controllo.py) con giochi finti e registri veri ridotti (test/dati/segnali).

Uso: python -I test/prova_controllo.py <cartella_temporanea_vuota>
TEMP, TMP, USERPROFILE e LOCALAPPDATA puntano a cartelle di prova (li' stanno i registri di Unity, i crash e le
opzioni dei giochi), i dati di PatchSmith in <cartella>/dati, il registro del traduttore dal vivo in <cartella>/ollama:
nessun gioco vero, nessun dato vero, nessuna rete, nessun programma avviato (tranne smith.py in sola lettura).
Copre:
- Unity: crash all'avvio dentro Application:.cctor (copia giusta) corretto con il punto d'ingresso di BepInEx; crash di
  un'altra copia del gioco (stesse cartelle LocalLow e %TEMP%) ignorato; LogOutput.log senza 'startup complete';
  Google che si ferma in Player-prev.log ("Failed: 'MASTER'"); comando del traduttore con un Python che non c'e';
  configurazione di XUnity cambiata; diario del traduttore dal vivo con frasi provvisorie e frasi perse; file
  temporanei rimasti; dopo la correzione un secondo controllo non fa nulla; un nuovo crash dopo la correzione propone
  "Ripristina originali" senza toccare nulla; con il gioco aperto non si scrive nulla; in sola lettura nemmeno;
- versione vecchia (come Rubinite): Google in pausa, nessun plugin, nessuna versione nel manifesto;
- file mancanti (antivirus): rimessi una volta; se tornano a mancare restano da fare (una volta per segnale);
- giochi con file tradotti: file rimessi originali dal gioco -> Riapplica; file aggiornati -> "Analizza e traduci";
  crash nel registro di Windows (eventi 1000 e 1026 veri, ridotti) -> "Ripristina originali" da confermare;
- GameMaker con la lingua aggiunta non scelta nel gioco; Unreal (lingua e pak non caricato), XNA (satelliti tolti);
- riga di comando `smith.py controlla --solo-leggi`, finestra dell'esito, rapporto, file di stato non cancellato;
- dalla verifica: registri oltre 2 MB (una partita lunga non e' un crash, gli errori in fondo contano),
  installazione interrotta (solo "Ripristina originali"), profilo senza cartella, registro di Unreal con la cartella
  del progetto, crash di Windows gia' passati, PatchSmith aggiornato (giochi Unity da ricontrollare), traduttore dal
  vivo senza traduttori, riga di comando con un profilo rovinato (gli altri giochi si controllano, codice 1);
- dalla revisione 0.16: le correzioni di "Correggi traduzioni" salvate con il gioco Unity aperto scritte dal controllo
  (a gioco chiuso, una volta; una riga cambiata a mano resta), un'altra versione con la configurazione da
  rimettere detta in un solo messaggio, il rapporto con le correzioni in attesa e le lettere dei font; "Ritraduci"
  con il traduttore locale non installato (le frasi perse si salvano, il pulsante e' "Installa il traduttore locale").
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import gioco_web_finto  # noqa: E402
from patchsmith import (__version__, adapter_unity, automatico, backend, componenti, controllo, esito,  # noqa: E402
                        lingua_gamemaker, lingua_ufficiale, ollama, progetto, rapporto, scansione, servizio_xunity,
                        sistema)

ESITI = []
SEGNALI = os.path.join(QUI, "dati", "segnali")
SMITH = os.path.join(progetto.CARTELLA_PROGETTO, "smith.py")
# i percorsi dei giochi nei registri veri (anonimizzati: due copie di One Million Coins)
COPIA_VECCHIA = "C:/Giochi/Copia vecchia/One Million Coins"
COPIA_NUOVA = "C:/Giochi/One Million Coins"
TRADUZIONI = {"MASTER": "GENERALE", "Hold [[0]] to leap": "Tieni premuto [[0]] per saltare",
              "Warm pool of light": "Calda pozza di luce", "Status lamp collar": "Collare della lampada di stato",
              "Captured drive belt": "Cinghia di trasmissione bloccata"}


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def leggi(percorso):
    with open(percorso, encoding="utf-8") as f:
        return f.read()


def scrivi(percorso, testo, data=None):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "w", encoding="utf-8", newline="") as f:
        f.write(testo)
    if data is not None:
        os.utime(percorso, (data, data))


def istantanea(cartella):
    """{percorso relativo: (dimensione, data)} di tutti i file (per dire "non e' cambiato nulla")."""
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for f in files:
            p = os.path.join(radice, f)
            st = os.stat(p)
            uscita[os.path.relpath(p, cartella)] = (st.st_size, st.st_mtime_ns)
    return uscita


def codici(problemi):
    return sorted(p.codice for p in problemi)


def per_codice(problemi, codice):
    return next((p for p in problemi if p.codice == codice), None)


# --- giochi finti ---------------------------------------------------------------------------------------

def crea_gioco_unity(cartella, nome_exe, versione, azienda, prodotto):
    dati = os.path.join(cartella, nome_exe + "_Data")
    os.makedirs(os.path.join(dati, "Managed"))
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    open(os.path.join(cartella, nome_exe + ".exe"), "wb").write(bytes(pe))
    open(os.path.join(dati, "globalgamemanagers"), "wb").write(b"\x00" * 48 + versione.encode("ascii") + b"\x00")
    open(os.path.join(dati, "Managed", "Assembly-CSharp.dll"), "wb").write(
        b"MZ" + "Start a new game".encode("utf-16-le") + b"\x00\x00")
    scrivi(os.path.join(dati, "app.info"), "%s\n%s" % (azienda, prodotto))


def _zip(percorso, voci):
    with zipfile.ZipFile(percorso, "w") as z:
        for nome, contenuto in voci.items():
            z.writestr(nome, contenuto)
    dati = open(percorso, "rb").read()
    return {"url": "file://non-usato", "file": os.path.basename(percorso), "dimensione": len(dati),
            "sha256": hashlib.sha256(dati).hexdigest(), "versione": "prova", "licenza": "-"}


def prepara_componenti(cartella):
    """Pacchetti finti di BepInEx e XUnity gia' 'scaricati' (nessun download)."""
    componenti.CARTELLA = cartella
    os.makedirs(cartella, exist_ok=True)
    componenti.CATALOGO = {
        "bepinex5_x64": _zip(os.path.join(cartella, "bep.zip"), {
            "winhttp.dll": b"proxy", "doorstop_config.ini": b"[General]\r\nenabled=true\r\n",
            ".doorstop_version": b"4.5.0", "BepInEx/core/BepInEx.dll": b"core",
            "BepInEx/core/BepInEx.Preloader.dll": b"pre"}),
        "xunity_bepinex5": _zip(os.path.join(cartella, "xu.zip"), {
            "BepInEx/core/XUnity.Common.dll": b"common",
            "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll": b"plugin"}),
    }


def _plugin_finto(cartella, info):
    """Al posto della compilazione con csc (le librerie del gioco finto non sono vere)."""
    dest = os.path.join(cartella, *adapter_unity.PLUGIN_SERVIZIO.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "wb").write(b"MZ plugin finto")


def profilo_unity(nome, gioco, **altro):
    percorso = os.path.join(automatico.cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, dict({"nome": nome, "cartella_gioco": gioco.replace(os.sep, "/"),
                                         "motore": "unity", "lingua_origine": "en", "lingua_destinazione": "it",
                                         "backend": "google", "dal_vivo": "servizio", "file": []}, **altro))
    return percorso


def con_percorso(testo, vecchio, nuovo):
    """Un registro vero con il percorso di un gioco al posto di un altro (con '/' e con '\\')."""
    return testo.replace(vecchio, nuovo.replace("\\", "/")).replace(vecchio.replace("/", "\\"),
                                                                     nuovo.replace("/", "\\"))


class Traduce(backend.Backend):
    nome = "prova"

    def traduci_righe(self, righe, origine, destinazione):
        return [TRADUZIONI.get(r, r) for r in righe]


# --- Unity -----------------------------------------------------------------------------------------------

def prova_unity(base):
    nome = "one-million-coins-prova"
    gioco = os.path.join(base, "giochi", "One Million Coins")
    crea_gioco_unity(gioco, "One Million Coins", "2022.3.10f1", "OyenUmiGames", "One Million Coins")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    controlla("installazione: manifesto con la versione di PatchSmith",
              adapter_unity.leggi_manifesto(gioco).get("patchsmith_versione") == __version__)
    controlla("da controllare: un gioco installato e mai controllato", percorso in controllo.da_controllare(eventi=[]))
    prima = time.time() - 600  # l'ultima scrittura di PatchSmith: 10 minuti fa
    dopo = time.time() - 120  # la partita: 2 minuti fa
    cfg = os.path.join(gioco, *adapter_unity.CONFIG_SERVIZIO.split("/"))
    comando = leggi(cfg)
    scrivi(cfg, comando.replace("Programma=", "Programma=C:\\non-esiste\\python\\pythonw.exe#"), prima)
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    # il primo avvio: BepInEx scrive la sua configurazione; XUnity con Google e un'opzione di prova accesa
    scrivi(os.path.join(gioco, *adapter_unity.CONFIG_BEPINEX.split("/")),
           "[Preloader.Entrypoint]\n\n## The local filename\nAssembly = UnityEngine.CoreModule.dll\n\n"
           "Type = Application\n\nMethod = .cctor\n", dopo)
    ini = os.path.join(gioco, *adapter_unity.CONFIG_XUNITY.split("/"))
    scrivi(ini, leggi(ini).replace("Endpoint=CustomTranslate", "Endpoint=GoogleTranslateV2").replace(
        "EnableTextPathLogging=False", "EnableTextPathLogging=True"), dopo)
    # crash: uno di questa copia, uno dell'altra copia (stesse cartelle %TEMP% e LocalLow)
    crashes = os.path.join(base, "temp", "OyenUmiGames", "One Million Coins", "Crashes")
    crash_vero = leggi(os.path.join(SEGNALI, "unity-crash-player.log"))
    scrivi(os.path.join(crashes, "Crash_2026-10-17_211234219", "Player.log"),
           con_percorso(crash_vero, COPIA_VECCHIA, gioco), dopo)
    scrivi(os.path.join(crashes, "Crash_2026-10-17_211331065", "Player.log"), crash_vero, dopo)
    locallow = os.path.join(base, "utente", "AppData", "LocalLow", "OyenUmiGames", "One Million Coins")
    scrivi(os.path.join(locallow, "Player-prev.log"),
           con_percorso(leggi(os.path.join(SEGNALI, "unity-player-errore-google.log")), COPIA_NUOVA, gioco), dopo)
    scrivi(os.path.join(locallow, "Player.log"), leggi(os.path.join(SEGNALI, "unity-player.log")), dopo)  # altra copia
    scrivi(os.path.join(gioco, *adapter_unity.LOG_BEPINEX.split("/")),
           leggi(os.path.join(SEGNALI, "bepinex-logoutput-crash.log")), dopo)
    # diario del traduttore dal vivo, memoria e cache di XUnity
    memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
    scrivi(memoria, json.dumps({"Tea and maintenance bench": "Tavolo per il tè e per la manutenzione"}))
    # (piu' una frase data dalla memoria che poi hai tolto a mano dalla memoria: non deve tornare)
    scrivi(servizio_xunity.percorso_diario(nome), leggi(os.path.join(SEGNALI, "diario-richieste.jsonl")) + "\n" +
           json.dumps({"ts": "2026-10-18T04:30:24", "testo": "Pool entry tread 3", "esito": "ok",
                       "backend": "memoria", "risposta": "Piattaforma della piscina 3"}) + "\n" +
           # (e un pezzo gia' in italiano lasciato com'era dal servizio: non e' una traduzione da salvare)
           json.dumps({"ts": "2026-10-18T04:30:25", "testo": "Generosamente donato da", "esito": "ok",
                       "backend": "gia' tradotta", "risposta": "Generosamente donato da"}) + "\n")
    cache = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    scrivi(cache, "MASTER=MASTER\nHold {{A}}s to leap=Hold {{A}}s to leap\nWarm pool of light=Warm piscina di luce\n"
                  "Continuous glove=Guanto (a mano)\nTea and maintenance bench=Tavolo per il tè e per la manutenzione\n")
    temporaneo = memoria + ".tmp"
    scrivi(temporaneo, "", prima)
    di_altri = os.path.join(os.path.dirname(memoria), "altro-gioco.it.json.tmp")  # di un altro gioco: non si tocca
    scrivi(di_altri, "", prima)

    # adapter_unity.stato: il crash non e' "BepInEx avviato"
    righe = []
    adapter_unity.stato(gioco, nome, "it", righe.append)
    controlla("stato: 'Chainloader started' senza 'startup complete' non vale come avviato",
              any("NO  BepInEx avviato" in r for r in righe))

    # 1) sola lettura: trova tutto, non scrive nulla
    tutto = istantanea(base)
    problemi = controllo.controlla(percorso, applica=False, log=silenzio)
    attesi = ["CONFIGURAZIONE", "CRASH_AVVIO", "FRASI_DA_RIFARE", "SERVIZIO", "TEMPORANEI", "TRADUTTORE_FERMO"]
    controlla("sola lettura: trovati crash all'avvio, Google fermo, comando rotto, configurazione, frasi, temporanei "
              "(%s)" % ", ".join(codici(problemi)), codici(problemi) == attesi)
    controlla("file temporaneo rimasto: detto al singolare",
              "è rimasto un file temporaneo" in per_codice(problemi, "TEMPORANEI").messaggio)
    crash = per_codice(problemi, "CRASH_AVVIO")
    controlla("crash: solo quello di questa copia del gioco (l'altra copia ha le stesse cartelle)",
              crash and crash.segnale == "crash:Crash_2026-10-17_211234219" and crash.sicuro)
    fermo = per_codice(problemi, "TRADUTTORE_FERMO")
    controlla("Google fermo: errori di Google e \"Failed: 'MASTER'\" dal Player-prev.log di questa copia",
              fermo and "MASTER" in fermo.messaggio and "errore di Google" in fermo.messaggio)
    controlla("sola lettura: tutto 'si correggerebbe da solo', niente scritto (nemmeno lo stato)",
              all(p.stato == "da_correggere" for p in problemi) and istantanea(base) == tutto
              and not os.path.exists(controllo.percorso_stato(percorso)))
    controlla("nessun messaggio con nomi tecnici per chi gioca (smith.py, JSON)",
              not any("smith.py" in p.messaggio or ".json" in p.messaggio for p in problemi))

    # 2) gioco aperto: nessuna correzione e nessuna scrittura
    vero = sistema.gioco_aperto
    sistema.gioco_aperto = lambda _c: True
    try:
        problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    finally:
        sistema.gioco_aperto = vero
    e = controllo.come_esito(problemi, percorso)
    controlla("gioco aperto: correzioni rimandate, niente scritto, esito GIOCO_APERTO con 'Riprova'",
              istantanea(base) == tutto and all(p.stato == "rimandato" for p in problemi if p.sicuro)
              and e.codice == "GIOCO_APERTO" and "chiudi_gioco" in e.azioni)
    controlla("gioco aperto: LogOutput.log incompleto non conta (il gioco potrebbe ancora caricare)",
              "CRASH_AVVIO" in codici(problemi))  # c'e' per la cartella del crash, non per il registro

    # 3) gioco chiuso: si corregge da solo
    righe = []
    problemi = controllo.controlla(percorso, applica=True, log=righe.append, traduttore="prova")
    e = controllo.come_esito(problemi, percorso)
    controlla("correzioni: tutte riuscite (%s)" % ", ".join("%s=%s" % (p.codice, p.stato) for p in problemi),
              all(p.stato == "corretto" for p in problemi) and e.codice == "OK" and e.extra["corretti"] == 6)
    controlla("crash all'avvio: BepInEx parte al primo MonoBehaviour (anche se il gioco non e' Unity 6)",
              adapter_unity.entrypoint_attuale(gioco) == "MonoBehaviour")
    c = leggi(ini)
    controlla("XUnity: di nuovo il traduttore di PatchSmith, controlli di XUnity spenti, opzione di prova spenta",
              "Endpoint=CustomTranslate" in c and "DisableSpamChecks=True" in c and "EnableTextPathLogging=False" in c
              and "EnableTextPathLogging=True" not in c)
    comando = adapter_unity.leggi_comando_servizio(gioco)
    controlla("comando del traduttore rifatto con il Python di adesso",
              os.path.exists(comando.get("Programma", "")) and "non-esiste" not in leggi(cfg))
    voci = adapter_unity.voci_cache_xunity(gioco, "it")
    controlla("cache: righe provvisorie sostituite con la traduzione nuova (rifiutata, rinviata, rilassata)",
              voci.get("MASTER") == "GENERALE" and voci.get("Hold {{A}}s to leap") == "Tieni premuto {{A}}s per saltare"
              and voci.get("Warm pool of light") == "Calda pozza di luce")
    controlla("cache: frasi senza risposta (errore, gioco in chiusura) aggiunte; la riga cambiata a mano resta",
              voci.get("Status lamp collar") == "Collare della lampada di stato"
              and voci.get("Captured drive belt") == "Cinghia di trasmissione bloccata"
              and voci.get("Continuous glove") == "Guanto (a mano)")
    salvata = json.loads(leggi(memoria))
    automatiche = json.loads(leggi(memoria[:-5] + ".automatiche.json"))
    controlla("memoria: frasi tradotte mentre si giocava e perse rimesse (anche tra le automatiche); quelle dalla "
              "memoria no", salvata.get("Continuous glove") == "Guanto a lunga durata"
              and salvata.get("Lift task light 5") == "Lampada da lavoro sollevabile 5"
              and automatiche.get("Continuous glove") == "Guanto a lunga durata"
              and "Tea and maintenance bench" not in automatiche and "Pool entry tread 3" not in salvata
              and "Generosamente donato da" not in salvata)
    controlla("file temporaneo rimasto tolto (solo quello della memoria di questo gioco)",
              not os.path.exists(temporaneo) and os.path.exists(di_altri))
    stato = controllo.leggi_stato(percorso)
    controlla("stato salvato in <nome>.controllo.json: tentativi, segnali visti, diario letto",
              set(stato.get("rimedi", {})) >= {"CRASH_AVVIO", "riconfigura", "FRASI_DA_RIFARE", "TEMPORANEI"}
              and stato.get("visti") and stato.get("diario_fino") == "2026-10-18T04:30:29"
              and stato.get("entrypoint_forzato") is True)
    controlla("registro: le correzioni con il loro esito", any("-> corretto:" in r for r in righe))
    controlla("non piu' da controllare all'apertura (segnali gia' visti)",
              percorso not in controllo.da_controllare(eventi=[]))
    finestra = esito.finestra(e, "controlla")
    controlla("finestra: 'Controllo dopo aver giocato' con 'Corretto da solo'",
              finestra["titolo"] == "Controllo dopo aver giocato" and "Corretto da solo:" in finestra["testo"]
              and "smith.py" not in finestra["testo"])

    # 4) un secondo controllo non fa nulla
    tutto = istantanea(gioco)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, traduttore="prova")
    controlla("secondo controllo: niente da fare, il gioco non cambia (%s)" % ", ".join(codici(problemi)),
              problemi == [] and istantanea(gioco) == tutto)

    # 5) il gioco crasha di nuovo dopo la correzione: non accetta mod, si propone Ripristina (da confermare)
    nuovo = time.time() + 5
    scrivi(os.path.join(crashes, "Crash_2026-10-18_090000000", "Player.log"),
           con_percorso(crash_vero, COPIA_VECCHIA, gioco), nuovo)
    controlla("nuovo crash: di nuovo da controllare all'apertura", percorso in controllo.da_controllare(eventi=[]))
    tutto = istantanea(gioco)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, traduttore="prova")
    e = controllo.come_esito(problemi, percorso)
    mod = per_codice(problemi, "CRASH_CON_MOD")
    controlla("crash anche dopo la correzione: 'non accetta mod', Ripristina proposto e non fatto",
              mod and not mod.sicuro and mod.stato == "da_fare" and "ripristina" in e.azioni
              and e.codice == "PARZIALE" and adapter_unity.manifesto_presente(gioco) and istantanea(gioco) == tutto)
    finestra = esito.finestra(e, "controlla")
    controlla("finestra: 'Qualcosa da fare' con 'Da fare:' e il pulsante 'Ripristina originali'",
              finestra["titolo"] == "Qualcosa da fare" and "Da fare:" in finestra["testo"]
              and ("Ripristina originali", "ripristina") in finestra["pulsanti"])
    salvato = controllo.esito_salvato(percorso)
    controlla("l'ultimo controllo resta salvato (per ricordarlo all'apertura)",
              salvato is not None and salvato.extra.get("da_fare") == 1)

    # riga di comando in sola lettura: codice 2 (c'e' qualcosa da fare), niente scritto
    tutto = istantanea(base)
    ambiente = dict(os.environ, PATCHSMITH_DATI=progetto.RADICE_DATI)
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", gioco, "--solo-leggi"], capture_output=True,
                       env=ambiente, timeout=300)
    uscita = r.stdout.decode("utf-8", "replace")
    controlla("riga di comando: 'smith.py controlla --solo-leggi' -> codice 2, il crash da fare, niente scritto",
              r.returncode == 2 and "non accetta" in uscita and "(sola lettura" in uscita and istantanea(base) == tutto)
    vuota = os.path.join(base, "nessun-gioco")
    os.makedirs(vuota)
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", vuota], capture_output=True, env=ambiente,
                       timeout=300)
    controlla("riga di comando: cartella senza traduzione -> codice 0", r.returncode == 0
              and "niente da controllare" in r.stdout.decode("utf-8", "replace"))

    # il file di stato non e' un profilo e Progetto non lo cancella (come il vecchio .stato.json)
    progetto.Progetto(percorso, silenzio)
    controlla("<nome>.controllo.json: non e' un profilo e resta dopo Progetto",
              os.path.exists(controllo.percorso_stato(percorso))
              and all(not p.endswith(".controllo.json") for p, _ in automatico.elenco_profili()))
    rapporto.cartella_desktop = lambda: os.path.join(base, "rapporti")
    zip_rapporto = rapporto.crea(gioco, os.path.join(base, "rapporti"))
    controlla("rapporto: c'e' il risultato del controllo", "profilo/%s.controllo.json" % nome
              in zipfile.ZipFile(zip_rapporto).namelist())
    return gioco


def prova_solo_crash(base):
    """Solo il crash all'avvio (nient'altro da correggere): dopo la correzione il crash vecchio non conta piu', anche
    se la correzione non riscrive il manifesto (conta l'ora della correzione salvata nello stato)."""
    nome = "solo-crash-prova"
    gioco = os.path.join(base, "giochi", "Solo Crash")
    crea_gioco_unity(gioco, "One Million Coins", "2022.3.10f1", "Studio Prova", "Solo Crash")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    prima, dopo = time.time() - 600, time.time() - 120
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO, adapter_unity.CONFIG_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    scrivi(os.path.join(base, "temp", "Studio Prova", "Solo Crash", "Crashes", "Crash_2026-10-17_211234219",
                        "Player.log"), con_percorso(leggi(os.path.join(SEGNALI, "unity-crash-player.log")),
                                                    COPIA_VECCHIA, gioco), dopo)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    controlla("solo il crash all'avvio: corretto (%s)" % ", ".join("%s=%s" % (p.codice, p.stato) for p in problemi),
              [(p.codice, p.stato) for p in problemi] == [("CRASH_AVVIO", "corretto")])
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    controlla("dopo la correzione il crash di prima non conta piu' (niente 'non accetta mod') (%s)"
              % ", ".join(codici(problemi)), problemi == [])


def prova_versione_vecchia(base):
    """Come Rubinite: installato da una versione vecchia (niente versione nel manifesto, niente traduttore di
    PatchSmith, XUnity con Google) e Google in pausa."""
    from patchsmith import limitatore
    nome = "rubinite-prova"
    gioco = os.path.join(base, "giochi", "Rubinite")
    crea_gioco_unity(gioco, "Rubinite", "6000.3.12f1", "Cup Dog Games", "Rubinite")
    percorso = profilo_unity(nome, gioco, backend="xunity-google")
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=False)
    m = json.loads(leggi(os.path.join(gioco, adapter_unity.MANIFESTO)))
    m.pop("patchsmith_versione")
    scrivi(os.path.join(gioco, adapter_unity.MANIFESTO), json.dumps(m))
    with open(percorso, encoding="utf-8") as f:
        p = json.load(f)
    p.pop("dal_vivo")
    scansione.salva_json(percorso, p)
    limitatore.Limitatore("google", log=None).bloccato(429, "prova")
    try:
        problemi = controllo.controlla(percorso, applica=False, log=silenzio)
        dal_vivo = per_codice(problemi, "DAL_VIVO")
        controlla("versione vecchia: 'frasi nuove a Google, in pausa (bloccato) fino ...' e versione precedente (%s)"
                  % ", ".join(codici(problemi)),
                  dal_vivo and "Google" in dal_vivo.messaggio and "bloccato" in dal_vivo.messaggio
                  and "fino" in dal_vivo.messaggio and "VERSIONE" in codici(problemi))
        problemi = controllo.controlla(percorso, applica=True, log=silenzio)
        dal_vivo = per_codice(problemi, "DAL_VIVO")
        controlla("versione vecchia: traduttore di PatchSmith preparato e versione aggiornata; senza traduttori "
                  "disponibili resta da fare 'Installa il traduttore locale'",
                  adapter_unity.servizio_attivo(gioco) and "Endpoint=CustomTranslate" in leggi(
                      os.path.join(gioco, *adapter_unity.CONFIG_XUNITY.split("/")))
                  and adapter_unity.leggi_manifesto(gioco).get("patchsmith_versione") == __version__
                  and dal_vivo.stato == "non_riuscito" and dal_vivo.azione == "installa_locale")
    finally:
        os.remove(limitatore.percorso_stato())
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    controlla("versione vecchia: dopo la correzione niente da fare (%s)" % ", ".join(codici(problemi)),
              problemi == [])

    # file installati tolti (antivirus): rimessi una volta; se tornano a mancare, restano da fare
    winhttp = os.path.join(gioco, "winhttp.dll")
    os.remove(winhttp)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    controlla("file mancante (antivirus): rimesso dai componenti gia' scaricati",
              codici(problemi) == ["FILE_MANCANTI"] and problemi[0].stato == "corretto" and os.path.exists(winhttp))
    os.remove(winhttp)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    e = controllo.come_esito(problemi, percorso)
    controlla("di nuovo mancante: gia' provato una volta, resta da fare (Riapplica e rapporto)",
              problemi and problemi[0].stato == "gia_tentato" and not os.path.exists(winhttp)
              and "riapplica" in e.azioni and "crea_rapporto" in e.azioni)


# --- giochi con file tradotti, crash di Windows --------------------------------------------------------

def prova_file(base):
    gioco = os.path.join(base, "giochi", "Lantern Keeper")
    rel = gioco_web_finto.crea(gioco)
    open(os.path.join(gioco, "Lantern Keeper.exe"), "wb").write(b"MZ")
    percorso, _ = automatico.prepara(gioco, "auto", "it", "google", silenzio)
    with open(percorso, encoding="utf-8") as f:
        profilo = json.load(f)
    scrivi(progetto.percorso_memoria(profilo), json.dumps({
        "Start a new game": "Inizia una nuova partita", "Options and settings": "Opzioni e impostazioni",
        "Quit to the desktop": "Esci al desktop"}, ensure_ascii=False))
    automatico.riapplica(gioco, silenzio)
    testi = os.path.join(gioco, *rel.split("/"))
    controlla("gioco web: tradotto, con la versione di PatchSmith nelle informazioni del file",
              "Inizia una nuova partita" in leggi(testi)
              and progetto.leggi_info(testi).get("patchsmith_versione") == __version__)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
    controlla("gioco web appena tradotto: niente da fare", problemi == [])
    # le informazioni con il nome delle versioni fino alla 0.12 (.patchsmith-info.json) contano come ultima scrittura
    vecchio = os.path.join(base, "giochi", "vecchio")
    os.makedirs(vecchio)
    scrivi(os.path.join(vecchio, "testi.json"), "{}")
    scrivi(os.path.join(vecchio, "testi.json" + progetto.SUFFISSO_INFO_VECCHIO),
           json.dumps({"hash_originale": "x", "hash_scritti": []}), time.time() - 3600)
    c = controllo._Contesto(os.path.join(base, "vecchio.json"), {"cartella_gioco": vecchio,
                                                                 "file": [{"percorso": "testi.json"}]}, {}, None, False,
                            None)
    controlla("ultima scrittura anche dalle informazioni con il nome vecchio (.patchsmith-info.json)",
              abs(controllo.riferimento(c) - (time.time() - 3600)) < 5)
    # un aggiornamento (o la verifica dei file di Steam) rimette l'originale
    shutil.copyfile(testi + progetto.SUFFISSO_BACKUP, testi)
    controlla("file rimesso originale dal gioco: da controllare all'apertura",
              percorso in controllo.da_controllare(eventi=[]))
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
    controlla("file rimesso originale dal gioco: traduzioni rimesse da sole (Riapplica)",
              codici(problemi) == ["RIPRISTINATO_DAL_GIOCO"] and problemi[0].stato == "corretto"
              and "Inizia una nuova partita" in leggi(testi))
    # un aggiornamento con testi nuovi: si dice di premere "Analizza e traduci", senza toccare nulla
    dati = json.loads(leggi(testi + progetto.SUFFISSO_BACKUP))
    dati["menu"]["nuovo"] = "A brand new menu entry"
    scrivi(testi, json.dumps(dati))
    tutto = istantanea(gioco)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
    e = controllo.come_esito(problemi, percorso)
    controlla("file aggiornato dal gioco: 'Analizza e traduci' da fare, nulla cambiato",
              codici(problemi) == ["AGGIORNATO"] and "traduci" in e.azioni and istantanea(gioco) == tutto)
    # crash nel registro di Windows dopo la traduzione: da confermare, non si ripristina da solo
    evento = {"id": "999", "istante": time.time() + 5, "codice": 1000, "app": "Lantern Keeper.exe",
              "percorso": os.path.join(gioco, "Lantern Keeper.exe"), "testo": ""}
    altro = dict(evento, id="998", percorso=os.path.join(base, "altro", "Lantern Keeper.exe"))
    controlla("crash di Windows del gioco: da controllare all'apertura",
              percorso in controllo.da_controllare(eventi=[evento, altro]))
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[evento, altro])
    crash = per_codice(problemi, "CRASH")
    controlla("crash di Windows: 'Ripristina originali' proposto, non fatto (un solo crash: l'altro e' di un'altra "
              "cartella)", crash and crash.stato == "da_fare" and crash.azione == "ripristina"
              and crash.segnale == "eventi:999" and istantanea(gioco) == tutto)
    controlla("crash di Windows gia' visto: non piu' da controllare all'apertura",
              percorso not in controllo.da_controllare(eventi=[evento, altro]))

    # eventi veri (ridotti): 1000 con il percorso dell'eseguibile, 1026 (.NET) con il solo nome, nella codifica di
    # Windows
    eventi = controllo.leggi_eventi(open(os.path.join(SEGNALI, "evento-crash-1000.xml"), "rb").read())
    controlla("evento 1000 vero: letto, del gioco giusto (non dell'altra copia)",
              len(eventi) == 1 and eventi[0]["app"] == "One Million Coins.exe"
              and len(controllo.crash_del_gioco(COPIA_VECCHIA, eventi, 0)) == 1
              and not controllo.crash_del_gioco(COPIA_NUOVA, eventi, 0))
    eventi = controllo.leggi_eventi(open(os.path.join(SEGNALI, "evento-crash-1026.xml"), "rb").read())
    con_exe = os.path.join(base, "giochi", "dotnet")
    os.makedirs(con_exe)
    open(os.path.join(con_exe, "EtwHostServiceUpdater.exe"), "wb").write(b"MZ")
    controlla("evento 1026 vero (codifica di Windows): il nome dell'eseguibile dal testo, riconosciuto nella cartella",
              len(eventi) == 1 and eventi[0]["app"] == "EtwHostServiceUpdater.exe"
              and len(controllo.crash_del_gioco(con_exe, eventi, 0)) == 1
              and not controllo.crash_del_gioco(gioco, eventi, 0))


def prova_gamemaker(base):
    """Lingua aggiunta (local/italiano.json): un'altra lingua scelta nel gioco si dice e basta; se il gioco ha tolto
    il file della lingua, Riapplica e poi la lingua si rimette (l'aveva cambiata il gioco, non chi gioca)."""
    gioco = os.path.join(base, "giochi", "Forager")
    os.makedirs(os.path.join(gioco, "local"))
    originale = os.path.join(gioco, "local", "english.json")
    scrivi(originale, json.dumps({"a": "Hello"}))
    nuova = os.path.join(gioco, "local", "italiano.json")
    scrivi(nuova, json.dumps({"a": "Ciao"}))
    info = progetto.crea_backup(originale, {"solo_creati": True, "creati": ["italiano.json"]})
    progetto.salva_info(originale, info)
    percorso = os.path.join(automatico.cartella_profili(), "forager-prova.json")
    scansione.salva_json(percorso, {"nome": "forager-prova", "cartella_gioco": gioco.replace(os.sep, "/"),
                                    "motore": "gamemaker", "lingua_origine": "en", "lingua_destinazione": "it",
                                    "backend": "google",
                                    "file": [{"percorso": "local/english.json", "formato": "json",
                                              "lingua_nuova": "local/italiano.json"}]})
    lingua_ufficiale._salva_registro(gioco, {"tipo": "gamemaker_opzioni", "file": "opzioni", "scritto": "italiano",
                                             "precedente": "english"})
    opzioni = os.path.join(base, "opzioni-forager.txt")
    scrivi(opzioni, "x")
    vero_cerca, vera_riapplica = lingua_gamemaker.cerca, automatico.riapplica
    vera_imposta = lingua_ufficiale.imposta_lingua_aggiunta
    chiamate = []
    lingua_gamemaker.cerca = lambda _c, lingue: (opzioni, ("k", "m", 0, "english"))
    automatico.riapplica = lambda cartella, log, prova=False, esito=None: chiamate.append("riapplica") or \
        esito.update(esito=esito_mod.Esito("OK", scritto=True, tradotti=1))
    lingua_ufficiale.imposta_lingua_aggiunta = lambda *a, **k: chiamate.append(("imposta", k.get("forza")))
    try:
        problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
        controlla("GameMaker: un'altra lingua scelta nel gioco -> solo detto (la scelta di chi gioca resta)",
                  codici(problemi) == ["LINGUA_AGGIUNTA"] and problemi[0].stato == "info" and not chiamate
                  and "Opzioni > Lingua" in problemi[0].messaggio)
        os.remove(nuova)
        problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
        controlla("GameMaker: lingua aggiunta tolta dal gioco -> Riapplica e lingua rimessa (forzata)",
                  codici(problemi) == ["RIPRISTINATO_DAL_GIOCO"] and problemi[0].stato == "corretto"
                  and chiamate == ["riapplica", ("imposta", True)])
    finally:
        lingua_gamemaker.cerca, automatico.riapplica = vero_cerca, vera_riapplica
        lingua_ufficiale.imposta_lingua_aggiunta = vera_imposta


esito_mod = esito


def prova_unreal_xna(base):
    from patchsmith import adapter_unreal, adapter_xna
    gioco = os.path.join(base, "giochi", "Dungeon Defenders Awakened")
    pak = os.path.join(gioco, "DDS", "Content", "Paks", "zz_Patchsmith_it_999_P.pak")
    os.makedirs(os.path.dirname(pak))
    open(pak, "wb").write(b"pak finto")
    rel = "DDS/Content/Paks/zz_Patchsmith_it_999_P.pak"
    prima = time.time() - 600
    scrivi(os.path.join(gioco, adapter_unreal.MANIFESTO), json.dumps({
        "versione": 1, "patchsmith": True, "lingua": "it", "profilo": "dda-prova",
        "file": {rel: hashlib.sha256(b"pak finto").hexdigest()}, "precedenti": {}, "sorgenti": {"x": "y"}}), prima)
    percorso = os.path.join(automatico.cartella_profili(), "dda-prova.json")
    scansione.salva_json(percorso, {"nome": "dda-prova", "cartella_gioco": gioco.replace(os.sep, "/"),
                                    "motore": "unreal", "lingua_origine": "en", "lingua_destinazione": "it",
                                    "backend": "google", "file": []})
    registro = os.path.join(base, "appdata", "DDS", "Saved", "Logs", "DDS.log")
    vero = leggi(os.path.join(SEGNALI, "unreal-dds.log"))
    scrivi(registro, vero.replace("configuration option (en)", "configuration option (fr)"))
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[])
    controlla("Unreal: il gioco usava un'altra lingua (dal suo registro) -> detto, pak caricato",
              codici(problemi) == ["LINGUA_UNREAL"] and "'fr'" in problemi[0].messaggio)
    scrivi(registro, "\n".join(r for r in vero.splitlines() if "zz_Patchsmith" not in r))
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[])
    controlla("Unreal: pak di PatchSmith non caricato -> rapporto", codici(problemi) == ["PAK_NON_CARICATO"]
              and problemi[0].azione == "crea_rapporto")
    with zipfile.ZipFile(rapporto.crea(gioco, os.path.join(base, "rapporti-unreal"))) as z:
        nomi = z.namelist()
    controlla("Unreal: il rapporto proposto ha il registro del gioco (DDS.log) da cui il controllo l'ha capito",
              "unreal/DDS.log" in nomi and "gioco/patchsmith-unreal.json" in nomi)
    os.remove(pak)
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[])
    controlla("Unreal: pak tolto dal gioco -> si rimette (Riapplica)", "TRADUZIONE_TOLTA" in codici(problemi)
              and per_codice(problemi, "TRADUZIONE_TOLTA").sicuro)

    xna = os.path.join(base, "giochi", "Rogue Legacy")
    os.makedirs(xna)
    open(os.path.join(xna, "RogueLegacy.exe"), "wb").write(b"MZ nuovo")
    scrivi(os.path.join(xna, adapter_xna.MANIFESTO), json.dumps({
        "versione": 1, "patchsmith": True, "lingua": "it", "profilo": "rl-prova",
        "file": {"it/RogueLegacy.resources.dll": "abc"}, "cartelle_create": ["it"],
        "sorgenti": {"RogueLegacy.exe": hashlib.sha256(b"MZ vecchio").hexdigest()}}))
    percorso = os.path.join(automatico.cartella_profili(), "rl-prova.json")
    scansione.salva_json(percorso, {"nome": "rl-prova", "cartella_gioco": xna.replace(os.sep, "/"), "motore": "xna",
                                    "lingua_origine": "en", "lingua_destinazione": "it", "backend": "google",
                                    "file": []})
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[])
    controlla("XNA: satellite tolto (si rimette) e gioco aggiornato (Analizza e traduci)",
              codici(problemi) == ["AGGIORNATO", "TRADUZIONE_TOLTA"])


def prova_dopo_verifica(base):
    """Casi trovati nella verifica del punto 2: registri grandi, installazione interrotta, profilo senza cartella,
    registro di Unreal con la cartella del progetto, crash di Windows gia' passati, PatchSmith aggiornato, riga di
    comando con un profilo rovinato, traduttore dal vivo senza traduttori."""
    import datetime
    from patchsmith import adapter_unreal
    nome = "lunga-prova"
    gioco = os.path.join(base, "giochi", "Partita Lunga")
    crea_gioco_unity(gioco, "Partita Lunga", "2022.3.10f1", "Studio Lungo", "Partita Lunga")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    prima, dopo = time.time() - 600, time.time() - 120
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO, adapter_unity.CONFIG_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    # una partita lunga e riuscita: "startup complete" all'inizio, poi oltre 2 MB di avvisi; il Player.log di questa
    # copia con il percorso all'inizio e gli errori di Google in fondo
    avvisi = "".join("[Warning: XUnity.AutoTranslator] Avviso di prova numero %d con un po' di testo\n" % i
                     for i in range(40000))
    scrivi(os.path.join(gioco, *adapter_unity.LOG_BEPINEX.split("/")),
           leggi(os.path.join(SEGNALI, "bepinex-logoutput.log")) + avvisi, dopo)
    righe = con_percorso(leggi(os.path.join(SEGNALI, "unity-player-errore-google.log")), COPIA_NUOVA,
                         gioco).split("\n")
    scrivi(os.path.join(base, "utente", "AppData", "LocalLow", "Studio Lungo", "Partita Lunga", "Player.log"),
           "\n".join(righe[:20]) + "\n" + avvisi + "\n".join(righe[20:]), dopo)
    # il traduttore dal vivo e' partito senza traduttori (registro del servizio, comune a tutti i giochi)
    ora = datetime.datetime.fromtimestamp(dopo).strftime("%Y-%m-%d %H:%M:%S")
    scrivi(os.path.join(base, "ollama", "servizio-xunity.log"),
           "%s Traduttore per XUnity in ascolto su http://127.0.0.1:1/translate (gioco: %s)\n"
           "%s Traduttori per le frasi nuove: nessuno (le frasi nuove restano in originale)\n" % (ora, nome, ora))
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[])
    controlla("registri grandi (oltre 2 MB): una partita riuscita non e' un crash all'avvio, gli errori in fondo al "
              "Player.log di questa copia contano; traduttore dal vivo senza traduttori (%s)" % ", ".join(
                  codici(problemi)), codici(problemi) == ["SERVIZIO_REGISTRO", "TRADUTTORE_FERMO"])
    corretto = controllo.Problema("TEMPORANEI", "info", "x", stato="corretto")
    controlla("esito: '1 problema corretto' (singolare)",
              "1 problema corretto." in controllo.come_esito([corretto], percorso).motivo)

    # PatchSmith aggiornato: i giochi Unity si ricontrollano all'apertura (versione da riconciliare)
    altro = os.path.join(automatico.cartella_profili(), "solo-crash-prova.json")
    vera = controllo.__version__
    prima_sel = altro in controllo.da_controllare(eventi=[])
    controllo.__version__ = "99.0.0"
    try:
        dopo_sel = altro in controllo.da_controllare(eventi=[])
    finally:
        controllo.__version__ = vera
    controlla("PatchSmith aggiornato: i giochi Unity gia' controllati tornano da controllare all'apertura",
              not prima_sel and dopo_sel)

    # installazione interrotta a meta': nessuna correzione da sola, si propone Ripristina
    interrotta = os.path.join(base, "giochi", "Interrotta")
    crea_gioco_unity(interrotta, "Interrotta", "2022.3.10f1", "Studio", "Interrotta")
    p_interrotta = profilo_unity("interrotta-prova", interrotta)
    adapter_unity.installa(interrotta, "en", "it", "interrotta-prova", silenzio, locale=p_interrotta)
    m = json.loads(leggi(os.path.join(interrotta, adapter_unity.MANIFESTO)))
    m["in_corso"] = True
    scrivi(os.path.join(interrotta, adapter_unity.MANIFESTO), json.dumps(m))
    os.remove(os.path.join(interrotta, "winhttp.dll"))
    tutto = istantanea(interrotta)
    problemi = controllo.controlla(p_interrotta, applica=True, log=silenzio, eventi=[])
    controlla("installazione interrotta: niente correzioni da sole, 'Ripristina originali' proposto (%s)"
              % ", ".join(codici(problemi)), codici(problemi) == ["INSTALLAZIONE_INTERROTTA"]
              and problemi[0].stato == "da_fare" and problemi[0].azione == "ripristina"
              and istantanea(interrotta) == tutto)

    # profilo senza cartella del gioco: niente da controllare (non la cartella attuale del programma)
    senza = os.path.join(automatico.cartella_profili(), "senza-cartella.json")
    scansione.salva_json(senza, {"nome": "senza-cartella", "motore": "gamemaker", "file": [{"percorso": "x.json"}]})
    righe = []
    problemi = controllo.controlla(senza, applica=False, log=righe.append)
    controlla("profilo senza cartella: niente da controllare, senza usare la cartella attuale",
              problemi == [] and not any(os.getcwd() in r for r in righe))
    os.remove(senza)

    # Unreal: il registro del gioco si trova anche se la cartella scelta e' quella del progetto
    attesi = [os.path.join(os.environ["LOCALAPPDATA"], "DDS", "Saved", "Logs", "DDS.log")]
    radice = os.path.join(base, "giochi", "Dungeon Defenders Awakened")
    pak = "zz_Patchsmith_it_999_P.pak"
    controlla("Unreal: registro del gioco dalla cartella del gioco e da quella del progetto",
              controllo._registri_unreal(radice, {"file": {"DDS/Content/Paks/" + pak: "h"}}) == attesi
              and controllo._registri_unreal(os.path.join(radice, "DDS"), {"file": {"Content/Paks/" + pak: "h"}})
              == attesi and adapter_unreal.PREFISSO_PAK in pak)

    # crash di Windows di prima dell'ultimo controllo (non suo: es. di prima di un Riapplica): non si ricontrolla a
    # ogni apertura
    web = automatico.trova_profilo(os.path.join(base, "giochi", "Lantern Keeper"))
    dal = controllo.leggi_stato(web).get("eventi_dal")
    vecchio = {"id": "997", "istante": dal - 30, "codice": 1000, "app": "Lantern Keeper.exe",
               "percorso": os.path.join(base, "giochi", "Lantern Keeper", "Lantern Keeper.exe"), "testo": ""}
    controlla("crash di Windows di prima dell'ultimo controllo: non da controllare all'apertura",
              web and dal and web not in controllo.da_controllare(eventi=[vecchio]))

    # riga di comando su tutti i giochi con un profilo rovinato: gli altri si controllano, codice 1
    rotto = os.path.join(automatico.cartella_profili(), "rotto-prova.json")
    scansione.salva_json(rotto, {"nome": "rotto-prova", "cartella_gioco": gioco.replace(os.sep, "/"),
                                 "motore": "gamemaker", "file": [{"percorso": 5}]})
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", "--solo-leggi"], capture_output=True,
                       env=dict(os.environ), timeout=600)
    uscita = r.stdout.decode("utf-8", "replace")
    os.remove(rotto)
    controlla("riga di comando con un profilo rovinato: errore detto, gli altri giochi controllati, codice 1",
              r.returncode == 1 and "ERRORE nel controllo di rotto-prova.json" in uscita
              and uscita.count("Controllo dopo aver giocato:") >= 6 and "Traceback" not in uscita
              and "Traceback" not in r.stderr.decode("utf-8", "replace"))


def prova_ritraduci_e_avvio(base):
    """Punto 5: all'apertura le frasi del diario non si ritraducono (restano da fare, con il pulsante "Ritraduci"),
    le frasi perse si salvano lo stesso; "non accetta mod" solo con un crash vero (non con il solo LogOutput.log
    incompleto di un gioco Unity 6 che parte gia' al primo MonoBehaviour)."""
    nome = "diario-rimandato-prova"
    gioco = os.path.join(base, "giochi", "Diario Rimandato")
    crea_gioco_unity(gioco, "Diario Rimandato", "2022.3.10f1", "Studio Diario", "Diario Rimandato")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    prima = time.time() - 600
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO, adapter_unity.CONFIG_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
    scrivi(memoria, json.dumps({"Tea and maintenance bench": "Tavolo per il tè e per la manutenzione"}))
    scrivi(servizio_xunity.percorso_diario(nome), leggi(os.path.join(SEGNALI, "diario-richieste.jsonl")))
    cache = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    scrivi(cache, "MASTER=MASTER\nWarm pool of light=Warm piscina di luce\n")
    chiamate, vero = [], controllo.crea_backend
    controllo.crea_backend = lambda nome_b, log=print: chiamate.append(nome_b) or Traduce()
    try:
        problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[], ritraduci=False)
        rimandate = per_codice(problemi, "FRASI_DA_RITRADURRE")
        perse = per_codice(problemi, "FRASI_DA_RIFARE")
        salvata = json.loads(leggi(memoria))
        controlla("all'apertura: frasi provvisorie da fare con 'Ritraduci' (5), nessun traduttore usato (%s)"
                  % ", ".join("%s=%s" % (p.codice, p.stato) for p in problemi),
                  rimandate and rimandate.stato == "da_fare" and rimandate.azione == "ritraduci"
                  and rimandate.extra.get("frasi") == 5 and chiamate == []
                  and adapter_unity.voci_cache_xunity(gioco, "it").get("MASTER") == "MASTER")
        controlla("all'apertura: le frasi tradotte ma perse si salvano lo stesso (senza traduttore)",
                  perse and perse.stato == "corretto" and salvata.get("Continuous glove") == "Guanto a lunga durata")
        stato = controllo.leggi_stato(percorso)
        e = controllo.come_esito(problemi, percorso)
        salvato = controllo.esito_salvato(percorso)
        finestra = esito.finestra(e, "controlla")
        controlla("all'apertura: il diario non va avanti; esito con 'ritraduci' (anche quello salvato, con 5 frasi)",
                  not stato.get("diario_fino") and "ritraduci" in e.azioni and salvato is not None
                  and any(p.get("frasi") == 5 and p.get("azione") == "ritraduci" for p in salvato.extra["controllo"])
                  and ("Ritraduci le frasi", "ritraduci") in finestra["pulsanti"])
        problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[], ritraduci=True)
        rifatte = per_codice(problemi, "FRASI_DA_RIFARE")
        controlla("pulsante 'Ritraduci': le frasi si ritraducono e vanno nella cache, il diario va avanti (%s)"
                  % ", ".join("%s=%s" % (p.codice, p.stato) for p in problemi),
                  rifatte and rifatte.stato == "corretto" and chiamate
                  and adapter_unity.voci_cache_xunity(gioco, "it").get("MASTER") == "GENERALE"
                  and controllo.leggi_stato(percorso).get("diario_fino") == "2026-10-18T04:30:29"
                  and not per_codice(problemi, "FRASI_DA_RITRADURRE"))
    finally:
        controllo.crea_backend = vero

    # Unity 6, gia' con il punto d'ingresso MonoBehaviour: LogOutput.log incompleto senza un crash = solo da sapere
    nome = "avvio-lento-prova"
    gioco = os.path.join(base, "giochi", "Avvio Lento")
    crea_gioco_unity(gioco, "Avvio Lento", "6000.3.12f1", "Studio Sei", "Avvio Lento")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    prima, dopo = time.time() - 600, time.time() - 120
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO, adapter_unity.CONFIG_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    scrivi(os.path.join(gioco, *adapter_unity.LOG_BEPINEX.split("/")),
           "[Message:   BepInEx] BepInEx 5.4.23.2 - Avvio Lento\n[Info   :   BepInEx] Running under Unity "
           "v6000.3.12f1\n[Message:   BepInEx] Preloader started\n", dopo)
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[])
    incompleto = per_codice(problemi, "AVVIO_INCOMPLETO")
    controlla("Unity 6 con MonoBehaviour, LogOutput.log incompleto e nessun crash: solo da sapere, niente 'non accetta "
              "mod' (%s)" % ", ".join(codici(problemi)), adapter_unity.entrypoint_attuale(gioco) == "MonoBehaviour"
              and incompleto and incompleto.stato == "info" and not per_codice(problemi, "CRASH_CON_MOD"))
    evento = {"id": "5001", "istante": dopo + 1, "codice": 1000, "app": "Avvio Lento.exe",
              "percorso": os.path.join(gioco, "Avvio Lento.exe"), "testo": ""}
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[evento])
    mod = per_codice(problemi, "CRASH_CON_MOD")
    controlla("...con un crash vero (registro di Windows): 'non accetta mod' da confermare, senza 'anche dopo la "
              "correzione' (non c'e' stata)", mod and not mod.sicuro and mod.azione == "ripristina"
              and "anche dopo" not in mod.messaggio)
    controllo._salva_stato(percorso, {"entrypoint_forzato": True})
    problemi = controllo.controlla(percorso, applica=False, log=silenzio, eventi=[evento])
    mod = per_codice(problemi, "CRASH_CON_MOD")
    controlla("...e dopo la correzione del punto d'ingresso: 'anche dopo la correzione'",
              mod and "anche dopo la correzione" in mod.messaggio)


def prova_correzioni_in_attesa(base):
    """Revisione 0.16: le correzioni di "Correggi traduzioni" salvate con il gioco Unity aperto (righe della cache di
    XUnity in attesa in .da_scrivere.json) le scrive il controllo, a gioco chiuso, una volta; una riga cambiata a mano
    dopo la correzione resta. Installato con una versione precedente e configurazione da rimettere: un solo messaggio
    (CONFIGURAZIONE che dice la versione), non anche VERSIONE."""
    from patchsmith import correzioni
    nome = "correzioni-in-attesa"
    gioco = os.path.join(base, "giochi", "Correzioni In Attesa")
    crea_gioco_unity(gioco, "Attesa", "2022.3.10f1", "Studio Attesa", "Correzioni In Attesa")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    profilo = json.loads(leggi(percorso))
    adapter_unity.scrivi_cache_xunity(gioco, "it", {"Start a new game": "Inizia nuovo gioco",
                                                    "Load game": "Carica partita", "Quit": "Esci"}, silenzio)
    controllo.controlla(percorso, applica=True, log=silenzio)  # (lo stato di partenza: niente da fare)
    controlla("correzioni in attesa: nessuna, nessun problema",
              "CORREZIONI_IN_ATTESA" not in codici(controllo.controlla(percorso, applica=False, log=silenzio)))
    # due correzioni salvate con il gioco aperto; poi una delle due righe cambiata a mano nel file
    correzioni.segna_da_scrivere(profilo, {"Start a new game": ("Inizia nuovo gioco", "Nuova partita"),
                                           "Load game": ("Carica partita", "Carica una partita")})
    cache = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    scrivi(cache, leggi(cache).replace("Load game=Carica partita", "Load game=Carica (a mano)"))
    controlla("correzioni in attesa: il controllo all'apertura le vede (segnali cambiati)",
              percorso in controllo.da_controllare(eventi=[]))
    tutto = istantanea(base)
    problemi = controllo.controlla(percorso, applica=False, log=silenzio)
    p = per_codice(problemi, "CORREZIONI_IN_ATTESA")
    controlla("correzioni in attesa: in sola lettura 'si correggerebbe da solo', con un messaggio per chi gioca, "
              "nulla scritto", p and p.stato == "da_correggere" and "2 tue correzioni" in p.messaggio
              and "Correggi traduzioni" in p.messaggio and istantanea(base) == tutto)
    vero = sistema.gioco_aperto
    sistema.gioco_aperto = lambda _c: True
    try:
        problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    finally:
        sistema.gioco_aperto = vero
    controlla("correzioni in attesa con il gioco aperto: rimandate, cache non toccata",
              per_codice(problemi, "CORREZIONI_IN_ATTESA").stato == "rimandato" and istantanea(base) == tutto)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    voci = adapter_unity.voci_cache_xunity(gioco, "it")
    p = per_codice(problemi, "CORREZIONI_IN_ATTESA")
    controlla("correzioni in attesa a gioco chiuso: scritta quella con la riga com'era, quella cambiata a mano resta, "
              "le altre righe identiche, niente piu' in attesa (%s)" % (p.dettaglio if p else None),
              p and p.stato == "corretto" and voci.get("Start a new game") == "Nuova partita"
              and voci.get("Load game") == "Carica (a mano)" and voci.get("Quit") == "Esci"
              and "una lasciata" in p.dettaglio and not correzioni.da_scrivere(profilo))
    controlla("correzioni in attesa: il controllo dopo non trova piu' nulla",
              "CORREZIONI_IN_ATTESA" not in codici(controllo.controlla(percorso, applica=True, log=silenzio)))
    # installato con una versione precedente e con la configurazione da rimettere: un messaggio solo
    m = json.loads(leggi(os.path.join(gioco, adapter_unity.MANIFESTO)))
    m["patchsmith_versione"] = "0.15.0"
    scrivi(os.path.join(gioco, adapter_unity.MANIFESTO), json.dumps(m))
    cfg = os.path.join(gioco, *adapter_unity.CONFIG_BEPINEX.split("/"))
    scrivi(cfg, leggi(cfg).replace("WriteUnityLog = true", "WriteUnityLog = false"))
    problemi = controllo.controlla(percorso, applica=False, log=silenzio)
    p = per_codice(problemi, "CONFIGURAZIONE")
    controlla("versione precedente e configurazione da rimettere: solo CONFIGURAZIONE, che dice la versione (%s)"
              % ", ".join(codici(problemi)), p and "VERSIONE" not in codici(problemi)
              and "un'altra versione di PatchSmith (0.15.0)" in p.messaggio)
    controllo.controlla(percorso, applica=True, log=silenzio)
    controlla("... dopo la correzione: versione nel manifesto aggiornata, niente da fare",
              adapter_unity.leggi_manifesto(gioco).get("patchsmith_versione") == __version__
              and controllo.controlla(percorso, applica=True, log=silenzio) == [])
    # il rapporto ha le correzioni in attesa e le lettere dei font letti (Unity Localization: font.json)
    correzioni.segna_da_scrivere(profilo, {"Quit": ("Esci", "Esci dal gioco")})
    font = progetto.cartella_dati("memory", "%s.unity" % nome, "font.json")
    scrivi(font, json.dumps({"descrizione": "prova"}))
    zip_ = rapporto.crea(gioco, os.path.join(base, "rapporti-attesa"))
    with zipfile.ZipFile(zip_) as z:
        nomi = z.namelist()
    controlla("rapporto: le correzioni non ancora scritte nel gioco e le lettere dei font",
              "traduzioni/%s.it.da_scrivere.json" % nome in nomi and "traduzioni/%s.font.json" % nome in nomi)
    os.remove(correzioni.percorso_da_scrivere(profilo))
    os.remove(font)


def prova_ritraduci_senza_traduttore(base):
    """Revisione 0.16: "Ritraduci" con il traduttore locale scelto ma non installato: le frasi perse si salvano lo
    stesso e il pulsante per rimediare e' "Installa il traduttore locale" (prima solo "Analizza e traduci")."""
    nome = "ritraduci-senza-traduttore"
    gioco = os.path.join(base, "giochi", "Senza Traduttore")
    crea_gioco_unity(gioco, "Senza Traduttore", "2022.3.10f1", "Studio Senza", "Senza Traduttore")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    prima = time.time() - 600
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO, adapter_unity.CONFIG_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    scrivi(servizio_xunity.percorso_diario(nome), leggi(os.path.join(SEGNALI, "diario-richieste.jsonl")))

    def assente(_nome, log=print):
        raise backend.ErroreBackend(backend.NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=backend.COMANDO_INSTALLA)

    vero = controllo.crea_backend
    controllo.crea_backend = assente
    try:
        problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[], ritraduci=True, traduttore="locale")
    finally:
        controllo.crea_backend = vero
    p = per_codice(problemi, "FRASI_DA_RIFARE")
    memoria = json.loads(leggi(progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})))
    finestra = esito.finestra(controllo.come_esito(problemi, percorso), "controlla")
    controlla("'Ritraduci' senza il traduttore locale: frasi perse salvate, 'Installa il traduttore locale' nella "
              "finestra, niente 'smith.py' (%s)" % (p.dettaglio if p else None),
              p and p.stato == "non_riuscito" and p.azione == "installa_locale"
              and memoria.get("Continuous glove") == "Guanto a lunga durata"
              and ("Installa il traduttore locale", "installa_locale") in finestra["pulsanti"]
              and "smith.py" not in finestra["testo"])


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    for variabile, nome in (("TEMP", "temp"), ("TMP", "temp"), ("USERPROFILE", "utente"),
                            ("LOCALAPPDATA", "appdata"), ("APPDATA", "appdata-roaming")):
        os.environ[variabile] = os.path.join(base, nome)
        os.makedirs(os.environ[variabile], exist_ok=True)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    os.environ["PATCHSMITH_DATI"] = progetto.RADICE_DATI  # anche per smith.py lanciato dalle prove
    automatico.CARTELLA_PROFILI = None
    ollama.cartella = lambda: os.path.join(base, "ollama")  # niente traduttore locale, registro di prova
    adapter_unity.in_esecuzione = lambda *_: False
    adapter_unity._compila_plugin = _plugin_finto
    controllo.crea_backend = lambda nome, log=print: Traduce()
    prepara_componenti(os.path.join(base, "componenti"))
    prova_unity(base)
    prova_solo_crash(base)
    prova_versione_vecchia(base)
    prova_file(base)
    prova_gamemaker(base)
    prova_unreal_xna(base)
    # tutti i giochi di prova insieme, in sola lettura: nessun errore, qualcosa da fare (codice 2), nulla scritto
    tutto = istantanea(base)
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", "--solo-leggi"], capture_output=True,
                       env=dict(os.environ), timeout=600)
    uscita = r.stdout.decode("utf-8", "replace")
    controlla("riga di comando: 'smith.py controlla --solo-leggi' su tutti i giochi -> codice 2, nessun errore, nulla "
              "scritto", r.returncode == 2 and "ERRORE" not in uscita and uscita.count("Controllo dopo aver giocato:")
              >= 6 and istantanea(base) == tutto)
    prova_dopo_verifica(base)
    prova_ritraduci_e_avvio(base)
    prova_correzioni_in_attesa(base)
    prova_ritraduci_senza_traduttore(base)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
