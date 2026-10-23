"""Tests of the after-play check (checks.py) with fake games and reduced real logs (test/data/signals).

Usage: python -I test/test_checks.py <empty_temp_folder>
TEMP, TMP, USERPROFILE and LOCALAPPDATA point to test folders (that is where Unity's logs, the crashes and the games'
options are), PatchSmith's data in <cartella>/dati, the live translator's log in <cartella>/ollama:
no real game, no real data, no network, no program started (except smith.py in read-only mode).
Covers:
- Unity: crash at startup inside Application:.cctor (right copy) fixed with BepInEx's entry point; crash of another
  copy of the game (same LocalLow and %TEMP% folders) ignored; LogOutput.log without 'startup complete';
  Google stopping in Player-prev.log ("Failed: 'MASTER'"); translator command with a Python that does not exist;
  XUnity configuration changed; live translator journal with provisional sentences and lost sentences; temporary
  files left behind; after the fix a second check does nothing; a new crash after the fix proposes
  "Restore originals" without touching anything; with the game open nothing is written; in read-only mode neither;
- old version (like Rubinite): Google paused, no plugin, no version in the manifest;
- missing files (antivirus): put back once; if they go missing again they remain to do (once per signal);
- games with translated files: files put back to the originals by the game -> Reapply; updated files -> "Analyze and
  translate"; crash in the Windows event log (real events 1000 and 1026, reduced) -> "Restore originals" to confirm;
- GameMaker with the added language not chosen in the game; Unreal (language and pak not loaded), XNA (satellites
  removed);
- command line `smith.py controlla --solo-leggi`, outcome window, report, status file not deleted;
- from the verification: logs over 2 MB (a long session is not a crash, the errors at the end count),
  interrupted installation (only "Restore originals"), game profile without folder, Unreal log with the project
  folder, Windows crashes already past, PatchSmith updated (Unity games to check again), live translator without
  translators, command line with a broken game profile (the other games are checked, code 1);
- from revision 0.16: the fixes from "Fix translations" saved with the Unity game open written by the check
  (with the game closed, once; a line changed by hand stays), another version with the configuration to put back
  told in a single message, the report with the pending fixes and the fonts' letters; "Re-translate" with the local
  translator not installed (the lost sentences are saved, the button is "Install the local translator").
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

import fake_web_game as gioco_web_finto  # noqa: E402
from patchsmith import (__version__, adapter_unity, auto as automatico, backend, components as componenti,
                        checks as controllo, outcome as esito, language_gamemaker as lingua_gamemaker,
                        official_language as lingua_ufficiale, ollama, project as progetto, report as rapporto,
                        scan as scansione, xunity_service as servizio_xunity, system as sistema)

ESITI = []
SEGNALI = os.path.join(QUI, "data", "signals")
SMITH = os.path.join(progetto.CARTELLA_PROGETTO, "smith.py")
# the game paths in the real logs (anonymized: two copies of One Million Coins)
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
    """{relative path: (size, date)} of all the files (to say "nothing changed")."""
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


# --- fake games -----------------------------------------------------------------------------------------

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
    """Fake BepInEx and XUnity packages already 'downloaded' (no download)."""
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
    """In place of the compilation with csc (the fake game's libraries are not real)."""
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
    """A real log with the path of one game in place of another (with '/' and with '\\')."""
    return testo.replace(vecchio, nuovo.replace("\\", "/")).replace(vecchio.replace("/", "\\"),
                                                                     nuovo.replace("/", "\\"))


class Traduce(backend.Backend):
    nome = "prova"

    def traduci_righe(self, righe, origine, destinazione):
        return [TRADUZIONI.get(r, r) for r in righe]


# --- Unity -----------------------------------------------------------------------------------------------

def test_unity(base):
    nome = "one-million-coins-prova"
    gioco = os.path.join(base, "giochi", "One Million Coins")
    crea_gioco_unity(gioco, "One Million Coins", "2022.3.10f1", "OyenUmiGames", "One Million Coins")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    controlla("installazione: manifesto con la versione di PatchSmith",
              adapter_unity.leggi_manifesto(gioco).get("patchsmith_versione") == __version__)
    controlla("da controllare: un gioco installato e mai controllato", percorso in controllo.da_controllare(eventi=[]))
    prima = time.time() - 600  # PatchSmith's last write: 10 minutes ago
    dopo = time.time() - 120  # the play session: 2 minutes ago
    cfg = os.path.join(gioco, *adapter_unity.CONFIG_SERVIZIO.split("/"))
    comando = leggi(cfg)
    scrivi(cfg, comando.replace("Programma=", "Programma=C:\\non-esiste\\python\\pythonw.exe#"), prima)
    for rel in (adapter_unity.MANIFESTO, adapter_unity.PLUGIN_SERVIZIO):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    # the first start: BepInEx writes its configuration; XUnity with Google and a debug option on
    scrivi(os.path.join(gioco, *adapter_unity.CONFIG_BEPINEX.split("/")),
           "[Preloader.Entrypoint]\n\n## The local filename\nAssembly = UnityEngine.CoreModule.dll\n\n"
           "Type = Application\n\nMethod = .cctor\n", dopo)
    ini = os.path.join(gioco, *adapter_unity.CONFIG_XUNITY.split("/"))
    scrivi(ini, leggi(ini).replace("Endpoint=CustomTranslate", "Endpoint=GoogleTranslateV2").replace(
        "EnableTextPathLogging=False", "EnableTextPathLogging=True"), dopo)
    # crashes: one from this copy, one from the other copy (same %TEMP% and LocalLow folders)
    crashes = os.path.join(base, "temp", "OyenUmiGames", "One Million Coins", "Crashes")
    crash_vero = leggi(os.path.join(SEGNALI, "unity-crash-player.log"))
    scrivi(os.path.join(crashes, "Crash_2026-10-17_211234219", "Player.log"),
           con_percorso(crash_vero, COPIA_VECCHIA, gioco), dopo)
    scrivi(os.path.join(crashes, "Crash_2026-10-17_211331065", "Player.log"), crash_vero, dopo)
    locallow = os.path.join(base, "utente", "AppData", "LocalLow", "OyenUmiGames", "One Million Coins")
    scrivi(os.path.join(locallow, "Player-prev.log"),
           con_percorso(leggi(os.path.join(SEGNALI, "unity-player-errore-google.log")), COPIA_NUOVA, gioco), dopo)
    scrivi(os.path.join(locallow, "Player.log"), leggi(os.path.join(SEGNALI, "unity-player.log")), dopo)  # other copy
    scrivi(os.path.join(gioco, *adapter_unity.LOG_BEPINEX.split("/")),
           leggi(os.path.join(SEGNALI, "bepinex-logoutput-crash.log")), dopo)
    # live translator journal, translation memory and XUnity cache
    memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
    scrivi(memoria, json.dumps({"Tea and maintenance bench": "Tavolo per il tè e per la manutenzione"}))
    # (plus a sentence given by the translation memory that you then removed by hand from it: it must not come back)
    scrivi(servizio_xunity.percorso_diario(nome), leggi(os.path.join(SEGNALI, "diario-richieste.jsonl")) + "\n" +
           json.dumps({"ts": "2026-10-18T04:30:24", "testo": "Pool entry tread 3", "esito": "ok",
                       "backend": "memoria", "risposta": "Piattaforma della piscina 3"}) + "\n" +
           # (and a piece already in Italian left as it was by the service: it is not a translation to save)
           json.dumps({"ts": "2026-10-18T04:30:25", "testo": "Generosamente donato da", "esito": "ok",
                       "backend": "gia' tradotta", "risposta": "Generosamente donato da"}) + "\n")
    cache = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    scrivi(cache, "MASTER=MASTER\nHold {{A}}s to leap=Hold {{A}}s to leap\nWarm pool of light=Warm piscina di luce\n"
                  "Continuous glove=Guanto (a mano)\nTea and maintenance bench=Tavolo per il tè e per la manutenzione\n")
    temporaneo = memoria + ".tmp"
    scrivi(temporaneo, "", prima)
    di_altri = os.path.join(os.path.dirname(memoria), "altro-gioco.it.json.tmp")  # of another game: not touched
    scrivi(di_altri, "", prima)

    # adapter_unity.stato: the crash is not "BepInEx started"
    righe = []
    adapter_unity.stato(gioco, nome, "it", righe.append)
    controlla("stato: 'Chainloader started' senza 'startup complete' non vale come avviato",
              any("NO  BepInEx avviato" in r for r in righe))

    # 1) read-only: finds everything, writes nothing
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

    # 2) game open: no fixes and no writes
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
              "CRASH_AVVIO" in codici(problemi))  # it is there because of the crash folder, not the log

    # 3) game closed: it fixes itself
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

    # 4) a second check does nothing
    tutto = istantanea(gioco)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, traduttore="prova")
    controlla("secondo controllo: niente da fare, il gioco non cambia (%s)" % ", ".join(codici(problemi)),
              problemi == [] and istantanea(gioco) == tutto)

    # 5) the game crashes again after the fix: it does not accept mods, Restore originals is proposed (to confirm)
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

    # read-only command line: code 2 (there is something to do), nothing written
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

    # the status file is not a game profile and Progetto does not delete it (like the old .stato.json)
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
    """Only the crash at startup (nothing else to fix): after the fix the old crash no longer counts, even if the
    fix does not rewrite the manifest (what counts is the time of the fix saved in the status)."""
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
    """Like Rubinite: installed by an old version (no version in the manifest, no PatchSmith translator, XUnity
    with Google) and Google paused."""
    from patchsmith import limiter as limitatore
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

    # installed files removed (antivirus): put back once; if they go missing again, they remain to do
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


# --- games with translated files, Windows crashes ------------------------------------------------------

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
    # the info files with the name used up to 0.12 (.patchsmith-info.json) count as the last write
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
    # an update (or Steam's file verification) puts the original back
    shutil.copyfile(testi + progetto.SUFFISSO_BACKUP, testi)
    controlla("file rimesso originale dal gioco: da controllare all'apertura",
              percorso in controllo.da_controllare(eventi=[]))
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
    controlla("file rimesso originale dal gioco: traduzioni rimesse da sole (Riapplica)",
              codici(problemi) == ["RIPRISTINATO_DAL_GIOCO"] and problemi[0].stato == "corretto"
              and "Inizia una nuova partita" in leggi(testi))
    # an update with new texts: the user is told to press "Analyze and translate", without touching anything
    dati = json.loads(leggi(testi + progetto.SUFFISSO_BACKUP))
    dati["menu"]["nuovo"] = "A brand new menu entry"
    scrivi(testi, json.dumps(dati))
    tutto = istantanea(gioco)
    problemi = controllo.controlla(percorso, applica=True, log=silenzio, eventi=[])
    e = controllo.come_esito(problemi, percorso)
    controlla("file aggiornato dal gioco: 'Analizza e traduci' da fare, nulla cambiato",
              codici(problemi) == ["AGGIORNATO"] and "traduci" in e.azioni and istantanea(gioco) == tutto)
    # crash in the Windows event log after the translation: to confirm, it is not restored automatically
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

    # real events (reduced): 1000 with the executable's path, 1026 (.NET) with only the name, in the Windows
    # encoding
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


def test_gamemaker(base):
    """Added language (local/italiano.json): another language chosen in the game is only reported; if the game
    removed the language file, Reapply and then the language is put back (the game changed it, not the player)."""
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
    """Cases found in the verification of point 2: large logs, interrupted installation, game profile without folder,
    Unreal log with the project folder, Windows crashes already past, PatchSmith updated, command line with a broken
    game profile, live translator without translators."""
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
    # a long, successful session: "startup complete" at the start, then over 2 MB of warnings; this copy's
    # Player.log with the path at the start and the Google errors at the end
    avvisi = "".join("[Warning: XUnity.AutoTranslator] Avviso di prova numero %d con un po' di testo\n" % i
                     for i in range(40000))
    scrivi(os.path.join(gioco, *adapter_unity.LOG_BEPINEX.split("/")),
           leggi(os.path.join(SEGNALI, "bepinex-logoutput.log")) + avvisi, dopo)
    righe = con_percorso(leggi(os.path.join(SEGNALI, "unity-player-errore-google.log")), COPIA_NUOVA,
                         gioco).split("\n")
    scrivi(os.path.join(base, "utente", "AppData", "LocalLow", "Studio Lungo", "Partita Lunga", "Player.log"),
           "\n".join(righe[:20]) + "\n" + avvisi + "\n".join(righe[20:]), dopo)
    # the live translator started without translators (service log, shared by all games)
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

    # PatchSmith updated: Unity games are checked again at startup (version to reconcile)
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

    # installation interrupted halfway: no automatic fix, Restore originals is proposed
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

    # game profile without the game's folder: nothing to check (not the program's current folder)
    senza = os.path.join(automatico.cartella_profili(), "senza-cartella.json")
    scansione.salva_json(senza, {"nome": "senza-cartella", "motore": "gamemaker", "file": [{"percorso": "x.json"}]})
    righe = []
    problemi = controllo.controlla(senza, applica=False, log=righe.append)
    controlla("profilo senza cartella: niente da controllare, senza usare la cartella attuale",
              problemi == [] and not any(os.getcwd() in r for r in righe))
    os.remove(senza)

    # Unreal: the game's log is found even if the chosen folder is the project's
    attesi = [os.path.join(os.environ["LOCALAPPDATA"], "DDS", "Saved", "Logs", "DDS.log")]
    radice = os.path.join(base, "giochi", "Dungeon Defenders Awakened")
    pak = "zz_Patchsmith_it_999_P.pak"
    controlla("Unreal: registro del gioco dalla cartella del gioco e da quella del progetto",
              controllo._registri_unreal(radice, {"file": {"DDS/Content/Paks/" + pak: "h"}}) == attesi
              and controllo._registri_unreal(os.path.join(radice, "DDS"), {"file": {"Content/Paks/" + pak: "h"}})
              == attesi and adapter_unreal.PREFISSO_PAK in pak)

    # Windows crash from before the last check (not its own: e.g. from before a Reapply): it is not checked again at
    # every startup
    web = automatico.trova_profilo(os.path.join(base, "giochi", "Lantern Keeper"))
    dal = controllo.leggi_stato(web).get("eventi_dal")
    vecchio = {"id": "997", "istante": dal - 30, "codice": 1000, "app": "Lantern Keeper.exe",
               "percorso": os.path.join(base, "giochi", "Lantern Keeper", "Lantern Keeper.exe"), "testo": ""}
    controlla("crash di Windows di prima dell'ultimo controllo: non da controllare all'apertura",
              web and dal and web not in controllo.da_controllare(eventi=[vecchio]))

    # command line on all games with a broken game profile: the others are checked, code 1
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
    """Point 5: at startup the journal's sentences are not re-translated (they remain to do, with the "Re-translate"
    button), the lost sentences are saved anyway; "does not accept mods" only with a real crash (not with just the
    incomplete LogOutput.log of a Unity 6 game that already starts at the first MonoBehaviour)."""
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

    # Unity 6, already with the MonoBehaviour entry point: incomplete LogOutput.log without a crash = only for info
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
    """Revision 0.16: the fixes from "Fix translations" saved with the Unity game open (XUnity cache lines pending in
    .da_scrivere.json) are written by the check, with the game closed, once; a line changed by hand after the fix
    stays. Installed with a previous version and configuration to put back: a single message (CONFIGURAZIONE that
    states the version), not VERSIONE as well."""
    from patchsmith import corrections as correzioni
    nome = "correzioni-in-attesa"
    gioco = os.path.join(base, "giochi", "Correzioni In Attesa")
    crea_gioco_unity(gioco, "Attesa", "2022.3.10f1", "Studio Attesa", "Correzioni In Attesa")
    percorso = profilo_unity(nome, gioco)
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=percorso)
    profilo = json.loads(leggi(percorso))
    adapter_unity.scrivi_cache_xunity(gioco, "it", {"Start a new game": "Inizia nuovo gioco",
                                                    "Load game": "Carica partita", "Quit": "Esci"}, silenzio)
    controllo.controlla(percorso, applica=True, log=silenzio)  # (the starting state: nothing to do)
    controlla("correzioni in attesa: nessuna, nessun problema",
              "CORREZIONI_IN_ATTESA" not in codici(controllo.controlla(percorso, applica=False, log=silenzio)))
    # two fixes saved with the game open; then one of the two lines changed by hand in the file
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
    # installed with a previous version and with the configuration to put back: a single message
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
    # the report has the pending fixes and the letters of the fonts read (Unity Localization: font.json)
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
    """Revision 0.16: "Re-translate" with the local translator chosen but not installed: the lost sentences are saved
    anyway and the button to fix it is "Install the local translator" (before, only "Analyze and translate")."""
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
    os.environ["PATCHSMITH_DATI"] = progetto.RADICE_DATI  # also for smith.py launched by the tests
    automatico.CARTELLA_PROFILI = None
    ollama.cartella = lambda: os.path.join(base, "ollama")  # no local translator, test log
    adapter_unity.in_esecuzione = lambda *_: False
    adapter_unity._compila_plugin = _plugin_finto
    controllo.crea_backend = lambda nome, log=print: Traduce()
    prepara_componenti(os.path.join(base, "componenti"))
    test_unity(base)
    prova_solo_crash(base)
    prova_versione_vecchia(base)
    prova_file(base)
    test_gamemaker(base)
    prova_unreal_xna(base)
    # all the test games together, read-only: no errors, something to do (code 2), nothing written
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
