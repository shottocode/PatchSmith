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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_web_game  # noqa: E402
from patchsmith import (__version__, adapter_unity, auto as automatic, backend, components,
                        checks, outcome, language_gamemaker,
                        official_language, ollama, project, report,
                        scan, xunity_service, system)

OUTCOMES = []
SIGNALS = os.path.join(HERE, "data", "signals")
SMITH = os.path.join(project.PROJECT_FOLDER, "smith.py")
# the game paths in the real logs (anonymized: two copies of One Million Coins)
OLD_COPY = "C:/Giochi/Copia vecchia/One Million Coins"
NEW_COPY = "C:/Giochi/One Million Coins"
TRANSLATIONS = {"MASTER": "GENERALE", "Hold [[0]] to leap": "Tieni premuto [[0]] per saltare",
              "Warm pool of light": "Calda pozza di luce", "Status lamp collar": "Collare della lampada di stato",
              "Captured drive belt": "Cinghia di trasmissione bloccata"}


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text, date=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if date is not None:
        os.utime(path, (date, date))


def snapshot(folder):
    """{relative path: (size, date)} of all the files (to say "nothing changed")."""
    output = {}
    for root, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            st = os.stat(p)
            output[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime_ns)
    return output


def codes(problems):
    return sorted(p.code for p in problems)


def by_code(problems, code):
    return next((p for p in problems if p.code == code), None)


# --- fake games -----------------------------------------------------------------------------------------

def create_unity_game(folder, exe_name, version, company, product):
    data = os.path.join(folder, exe_name + "_Data")
    os.makedirs(os.path.join(data, "Managed"))
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    open(os.path.join(folder, exe_name + ".exe"), "wb").write(bytes(pe))
    open(os.path.join(data, "globalgamemanagers"), "wb").write(b"\x00" * 48 + version.encode("ascii") + b"\x00")
    open(os.path.join(data, "Managed", "Assembly-CSharp.dll"), "wb").write(
        b"MZ" + "Start a new game".encode("utf-16-le") + b"\x00\x00")
    write(os.path.join(data, "app.info"), "%s\n%s" % (company, product))


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    data = open(path, "rb").read()
    return {"url": "file://non-usato", "file": os.path.basename(path), "dimensione": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "versione": "prova", "licenza": "-"}


def prepare_components(folder):
    """Fake BepInEx and XUnity packages already 'downloaded' (no download)."""
    components.FOLDER = folder
    os.makedirs(folder, exist_ok=True)
    components.CATALOG = {
        "bepinex5_x64": _zip(os.path.join(folder, "bep.zip"), {
            "winhttp.dll": b"proxy", "doorstop_config.ini": b"[General]\r\nenabled=true\r\n",
            ".doorstop_version": b"4.5.0", "BepInEx/core/BepInEx.dll": b"core",
            "BepInEx/core/BepInEx.Preloader.dll": b"pre"}),
        "xunity_bepinex5": _zip(os.path.join(folder, "xu.zip"), {
            "BepInEx/core/XUnity.Common.dll": b"common",
            "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll": b"plugin"}),
    }


def _fake_plugin(folder, info):
    """In place of the compilation with csc (the fake game's libraries are not real)."""
    dest = os.path.join(folder, *adapter_unity.SERVICE_PLUGIN.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "wb").write(b"MZ plugin finto")


def unity_profile(name, game, **other):
    path = os.path.join(automatic.profiles_folder(), name + ".json")
    scan.save_json(path, dict({"nome": name, "cartella_gioco": game.replace(os.sep, "/"),
                                         "motore": "unity", "lingua_origine": "en", "lingua_destinazione": "it",
                                         "backend": "google", "dal_vivo": "servizio", "file": []}, **other))
    return path


def with_path(text, old, new):
    """A real log with the path of one game in place of another (with '/' and with '\\')."""
    return text.replace(old, new.replace("\\", "/")).replace(old.replace("/", "\\"),
                                                                     new.replace("/", "\\"))


class DictTranslator(backend.Backend):
    name = "prova"

    def translate_lines(self, lines, origin, dest_lang):
        return [TRANSLATIONS.get(r, r) for r in lines]


# --- Unity -----------------------------------------------------------------------------------------------

def test_unity(base):
    name = "one-million-coins-prova"
    game = os.path.join(base, "giochi", "One Million Coins")
    create_unity_game(game, "One Million Coins", "2022.3.10f1", "OyenUmiGames", "One Million Coins")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    check("installazione: manifesto con la versione di PatchSmith",
              adapter_unity.read_manifest(game).get("patchsmith_versione") == __version__)
    check("da controllare: un gioco installato e mai controllato", path in checks.to_check(events=[]))
    before = time.time() - 600  # PatchSmith's last write: 10 minutes ago
    after = time.time() - 120  # the play session: 2 minutes ago
    cfg = os.path.join(game, *adapter_unity.SERVICE_CONFIG.split("/"))
    command = read_file(cfg)
    write(cfg, command.replace("Programma=", "Programma=C:\\non-esiste\\python\\pythonw.exe#"), before)
    for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_PLUGIN):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    # the first start: BepInEx writes its configuration; XUnity with Google and a debug option on
    write(os.path.join(game, *adapter_unity.CONFIG_BEPINEX.split("/")),
           "[Preloader.Entrypoint]\n\n## The local filename\nAssembly = UnityEngine.CoreModule.dll\n\n"
           "Type = Application\n\nMethod = .cctor\n", after)
    ini = os.path.join(game, *adapter_unity.CONFIG_XUNITY.split("/"))
    write(ini, read_file(ini).replace("Endpoint=CustomTranslate", "Endpoint=GoogleTranslateV2").replace(
        "EnableTextPathLogging=False", "EnableTextPathLogging=True"), after)
    # crashes: one from this copy, one from the other copy (same %TEMP% and LocalLow folders)
    crashes = os.path.join(base, "temp", "OyenUmiGames", "One Million Coins", "Crashes")
    real_crash = read_file(os.path.join(SIGNALS, "unity-crash-player.log"))
    write(os.path.join(crashes, "Crash_2026-10-17_211234219", "Player.log"),
           with_path(real_crash, OLD_COPY, game), after)
    write(os.path.join(crashes, "Crash_2026-10-17_211331065", "Player.log"), real_crash, after)
    locallow = os.path.join(base, "utente", "AppData", "LocalLow", "OyenUmiGames", "One Million Coins")
    write(os.path.join(locallow, "Player-prev.log"),
           with_path(read_file(os.path.join(SIGNALS, "unity-player-errore-google.log")), NEW_COPY, game), after)
    write(os.path.join(locallow, "Player.log"),
           read_file(os.path.join(SIGNALS, "unity-player.log")), after)  # other copy
    write(os.path.join(game, *adapter_unity.BEPINEX_LOG.split("/")),
           read_file(os.path.join(SIGNALS, "bepinex-logoutput-crash.log")), after)
    # live translator journal, translation memory and XUnity cache
    memory = project.memory_path({"nome": name, "lingua_destinazione": "it"})
    write(memory, json.dumps({"Tea and maintenance bench": "Tavolo per il tè e per la manutenzione"}))
    # (plus a sentence given by the translation memory that you then removed by hand from it: it must not come back)
    write(xunity_service.journal_path(name), read_file(os.path.join(SIGNALS, "diario-richieste.jsonl")) + "\n" +
           json.dumps({"ts": "2026-10-18T04:30:24", "testo": "Pool entry tread 3", "esito": "ok",
                       "backend": "memoria", "risposta": "Piattaforma della piscina 3"}) + "\n" +
           # (and a piece already in Italian left as it was by the service: it is not a translation to save)
           json.dumps({"ts": "2026-10-18T04:30:25", "testo": "Generosamente donato da", "esito": "ok",
                       "backend": "gia' tradotta", "risposta": "Generosamente donato da"}) + "\n")
    cache = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    write(cache, "MASTER=MASTER\nHold {{A}}s to leap=Hold {{A}}s to leap\nWarm pool of light=Warm piscina di luce\n"
                  "Continuous glove=Guanto (a mano)\nTea and maintenance bench=Tavolo per il tè e per la manutenzione\n")
    temp_file = memory + ".tmp"
    write(temp_file, "", before)
    of_others = os.path.join(os.path.dirname(memory), "altro-gioco.it.json.tmp")  # of another game: not touched
    write(of_others, "", before)

    # adapter_unity.state: the crash is not "BepInEx started"
    lines = []
    adapter_unity.state(game, name, "it", lines.append)
    check("stato: 'Chainloader started' senza 'startup complete' non vale come avviato",
              any("NO  BepInEx avviato" in r for r in lines))

    # 1) read-only: finds everything, writes nothing
    whole = snapshot(base)
    problems = checks.check(path, apply=False, log=silent)
    expected_list = ["CONFIGURAZIONE", "CRASH_AVVIO", "FRASI_DA_RIFARE", "SERVIZIO", "TEMPORANEI", "TRADUTTORE_FERMO"]
    check("sola lettura: trovati crash all'avvio, Google fermo, comando rotto, configurazione, frasi, temporanei "
              "(%s)" % ", ".join(codes(problems)), codes(problems) == expected_list)
    check("file temporaneo rimasto: detto al singolare",
              "è rimasto un file temporaneo" in by_code(problems, "TEMPORANEI").message)
    crash = by_code(problems, "CRASH_AVVIO")
    check("crash: solo quello di questa copia del gioco (l'altra copia ha le stesse cartelle)",
              crash and crash.signal == "crash:Crash_2026-10-17_211234219" and crash.safe)
    halted = by_code(problems, "TRADUTTORE_FERMO")
    check("Google fermo: errori di Google e \"Failed: 'MASTER'\" dal Player-prev.log di questa copia",
              halted and "MASTER" in halted.message and "errore di Google" in halted.message)
    check("sola lettura: tutto 'si correggerebbe da solo', niente scritto (nemmeno lo stato)",
              all(p.state == "da_correggere" for p in problems) and snapshot(base) == whole
              and not os.path.exists(checks.state_path(path)))
    check("nessun messaggio con nomi tecnici per chi gioca (smith.py, JSON)",
              not any("smith.py" in p.message or ".json" in p.message for p in problems))

    # 2) game open: no fixes and no writes
    real = system.game_open
    system.game_open = lambda _c: True
    try:
        problems = checks.check(path, apply=True, log=silent)
    finally:
        system.game_open = real
    e = checks.as_outcome(problems, path)
    check("gioco aperto: correzioni rimandate, niente scritto, esito GIOCO_APERTO con 'Riprova'",
              snapshot(base) == whole and all(p.state == "rimandato" for p in problems if p.safe)
              and e.code == "GIOCO_APERTO" and "chiudi_gioco" in e.actions)
    check("gioco aperto: LogOutput.log incompleto non conta (il gioco potrebbe ancora caricare)",
              "CRASH_AVVIO" in codes(problems))  # it is there because of the crash folder, not the log

    # 3) game closed: it fixes itself
    lines = []
    problems = checks.check(path, apply=True, log=lines.append, translator="prova")
    e = checks.as_outcome(problems, path)
    check("correzioni: tutte riuscite (%s)" % ", ".join("%s=%s" % (p.code, p.state) for p in problems),
              all(p.state == "corretto" for p in problems) and e.code == "OK" and e.extra["corretti"] == 6)
    check("crash all'avvio: BepInEx parte al primo MonoBehaviour (anche se il gioco non e' Unity 6)",
              adapter_unity.current_entrypoint(game) == "MonoBehaviour")
    c = read_file(ini)
    check("XUnity: di nuovo il traduttore di PatchSmith, controlli di XUnity spenti, opzione di prova spenta",
              "Endpoint=CustomTranslate" in c and "DisableSpamChecks=True" in c and "EnableTextPathLogging=False" in c
              and "EnableTextPathLogging=True" not in c)
    command = adapter_unity.read_service_command(game)
    check("comando del traduttore rifatto con il Python di adesso",
              os.path.exists(command.get("Programma", "")) and "non-esiste" not in read_file(cfg))
    entries = adapter_unity.xunity_cache_entries(game, "it")
    check("cache: righe provvisorie sostituite con la traduzione nuova (rifiutata, rinviata, rilassata)",
              entries.get("MASTER") == "GENERALE"
              and entries.get("Hold {{A}}s to leap") == "Tieni premuto {{A}}s per saltare"
              and entries.get("Warm pool of light") == "Calda pozza di luce")
    check("cache: frasi senza risposta (errore, gioco in chiusura) aggiunte; la riga cambiata a mano resta",
              entries.get("Status lamp collar") == "Collare della lampada di stato"
              and entries.get("Captured drive belt") == "Cinghia di trasmissione bloccata"
              and entries.get("Continuous glove") == "Guanto (a mano)")
    saved = json.loads(read_file(memory))
    machine = json.loads(read_file(memory[:-5] + ".automatiche.json"))
    check("memoria: frasi tradotte mentre si giocava e perse rimesse (anche tra le automatiche); quelle dalla "
              "memoria no", saved.get("Continuous glove") == "Guanto a lunga durata"
              and saved.get("Lift task light 5") == "Lampada da lavoro sollevabile 5"
              and machine.get("Continuous glove") == "Guanto a lunga durata"
              and "Tea and maintenance bench" not in machine and "Pool entry tread 3" not in saved
              and "Generosamente donato da" not in saved)
    check("file temporaneo rimasto tolto (solo quello della memoria di questo gioco)",
              not os.path.exists(temp_file) and os.path.exists(of_others))
    state = checks.read_state(path)
    check("stato salvato in <nome>.controllo.json: tentativi, segnali visti, diario letto",
              set(state.get("rimedi", {})) >= {"CRASH_AVVIO", "riconfigura", "FRASI_DA_RIFARE", "TEMPORANEI"}
              and state.get("visti") and state.get("diario_fino") == "2026-10-18T04:30:29"
              and state.get("entrypoint_forzato") is True)
    check("registro: le correzioni con il loro esito", any("-> corretto:" in r for r in lines))
    check("non piu' da controllare all'apertura (segnali gia' visti)",
              path not in checks.to_check(events=[]))
    window = outcome.window(e, "controlla")
    check("finestra: 'Controllo dopo aver giocato' con 'Corretto da solo'",
              window["titolo"] == "Controllo dopo aver giocato" and "Corretto da solo:" in window["testo"]
              and "smith.py" not in window["testo"])

    # 4) a second check does nothing
    whole = snapshot(game)
    problems = checks.check(path, apply=True, log=silent, translator="prova")
    check("secondo controllo: niente da fare, il gioco non cambia (%s)" % ", ".join(codes(problems)),
              problems == [] and snapshot(game) == whole)

    # 5) the game crashes again after the fix: it does not accept mods, Restore originals is proposed (to confirm)
    new = time.time() + 5
    write(os.path.join(crashes, "Crash_2026-10-18_090000000", "Player.log"),
           with_path(real_crash, OLD_COPY, game), new)
    check("nuovo crash: di nuovo da controllare all'apertura", path in checks.to_check(events=[]))
    whole = snapshot(game)
    problems = checks.check(path, apply=True, log=silent, translator="prova")
    e = checks.as_outcome(problems, path)
    mod = by_code(problems, "CRASH_CON_MOD")
    check("crash anche dopo la correzione: 'non accetta mod', Ripristina proposto e non fatto",
              mod and not mod.safe and mod.state == "da_fare" and "ripristina" in e.actions
              and e.code == "PARZIALE" and adapter_unity.manifest_present(game) and snapshot(game) == whole)
    window = outcome.window(e, "controlla")
    check("finestra: 'Qualcosa da fare' con 'Da fare:' e il pulsante 'Ripristina originali'",
              window["titolo"] == "Qualcosa da fare" and "Da fare:" in window["testo"]
              and ("Ripristina originali", "ripristina") in window["pulsanti"])
    stored = checks.saved_outcome(path)
    check("l'ultimo controllo resta salvato (per ricordarlo all'apertura)",
              stored is not None and stored.extra.get("da_fare") == 1)

    # read-only command line: code 2 (there is something to do), nothing written
    whole = snapshot(base)
    environment = dict(os.environ, PATCHSMITH_DATI=project.DATA_ROOT)
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", game, "--solo-leggi"], capture_output=True,
                       env=environment, timeout=300)
    output = r.stdout.decode("utf-8", "replace")
    check("riga di comando: 'smith.py controlla --solo-leggi' -> codice 2, il crash da fare, niente scritto",
              r.returncode == 2 and "non accetta" in output and "(sola lettura" in output and snapshot(base) == whole)
    empty = os.path.join(base, "nessun-gioco")
    os.makedirs(empty)
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", empty], capture_output=True, env=environment,
                       timeout=300)
    check("riga di comando: cartella senza traduzione -> codice 0", r.returncode == 0
              and "niente da controllare" in r.stdout.decode("utf-8", "replace"))

    # the status file is not a game profile and Project does not delete it (like the old .state.json)
    project.Project(path, silent)
    check("<nome>.controllo.json: non e' un profilo e resta dopo Progetto",
              os.path.exists(checks.state_path(path))
              and all(not p.endswith(".controllo.json") for p, _ in automatic.list_profiles()))
    report.desktop_folder = lambda: os.path.join(base, "rapporti")
    report_zip = report.create(game, os.path.join(base, "rapporti"))
    check("rapporto: c'e' il risultato del controllo", "profilo/%s.controllo.json" % name
              in zipfile.ZipFile(report_zip).namelist())
    return game


def test_crash_only(base):
    """Only the crash at startup (nothing else to fix): after the fix the old crash no longer counts, even if the
    fix does not rewrite the manifest (what counts is the time of the fix saved in the status)."""
    name = "solo-crash-prova"
    game = os.path.join(base, "giochi", "Solo Crash")
    create_unity_game(game, "One Million Coins", "2022.3.10f1", "Studio Prova", "Solo Crash")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    before, after = time.time() - 600, time.time() - 120
    for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_PLUGIN, adapter_unity.SERVICE_CONFIG):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    write(os.path.join(base, "temp", "Studio Prova", "Solo Crash", "Crashes", "Crash_2026-10-17_211234219",
                        "Player.log"), with_path(read_file(os.path.join(SIGNALS, "unity-crash-player.log")),
                                                    OLD_COPY, game), after)
    problems = checks.check(path, apply=True, log=silent)
    check("solo il crash all'avvio: corretto (%s)" % ", ".join("%s=%s" % (p.code, p.state) for p in problems),
              [(p.code, p.state) for p in problems] == [("CRASH_AVVIO", "corretto")])
    problems = checks.check(path, apply=True, log=silent)
    check("dopo la correzione il crash di prima non conta piu' (niente 'non accetta mod') (%s)"
              % ", ".join(codes(problems)), problems == [])


def test_old_version(base):
    """Like Rubinite: installed by an old version (no version in the manifest, no PatchSmith translator, XUnity
    with Google) and Google paused."""
    from patchsmith import limiter
    name = "rubinite-prova"
    game = os.path.join(base, "giochi", "Rubinite")
    create_unity_game(game, "Rubinite", "6000.3.12f1", "Cup Dog Games", "Rubinite")
    path = unity_profile(name, game, backend="xunity-google")
    adapter_unity.install(game, "en", "it", name, silent, local=False)
    m = json.loads(read_file(os.path.join(game, adapter_unity.MANIFEST)))
    m.pop("patchsmith_versione")
    write(os.path.join(game, adapter_unity.MANIFEST), json.dumps(m))
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    p.pop("dal_vivo")
    scan.save_json(path, p)
    limiter.Limiter("google", log=None).blocked(429, "prova")
    try:
        problems = checks.check(path, apply=False, log=silent)
        live = by_code(problems, "DAL_VIVO")
        check("versione vecchia: 'frasi nuove a Google, in pausa (bloccato) fino ...' e versione precedente (%s)"
                  % ", ".join(codes(problems)),
                  live and "Google" in live.message and "bloccato" in live.message
                  and "fino" in live.message and "VERSIONE" in codes(problems))
        problems = checks.check(path, apply=True, log=silent)
        live = by_code(problems, "DAL_VIVO")
        check("versione vecchia: traduttore di PatchSmith preparato e versione aggiornata; senza traduttori "
                  "disponibili resta da fare 'Installa il traduttore locale'",
                  adapter_unity.service_installed(game) and "Endpoint=CustomTranslate" in read_file(
                      os.path.join(game, *adapter_unity.CONFIG_XUNITY.split("/")))
                  and adapter_unity.read_manifest(game).get("patchsmith_versione") == __version__
                  and live.state == "non_riuscito" and live.action == "installa_locale")
    finally:
        os.remove(limiter.state_path())
    problems = checks.check(path, apply=True, log=silent)
    check("versione vecchia: dopo la correzione niente da fare (%s)" % ", ".join(codes(problems)),
              problems == [])

    # installed files removed (antivirus): put back once; if they go missing again, they remain to do
    winhttp = os.path.join(game, "winhttp.dll")
    os.remove(winhttp)
    problems = checks.check(path, apply=True, log=silent)
    check("file mancante (antivirus): rimesso dai componenti gia' scaricati",
              codes(problems) == ["FILE_MANCANTI"] and problems[0].state == "corretto" and os.path.exists(winhttp))
    os.remove(winhttp)
    problems = checks.check(path, apply=True, log=silent)
    e = checks.as_outcome(problems, path)
    check("di nuovo mancante: gia' provato una volta, resta da fare (Riapplica e rapporto)",
              problems and problems[0].state == "gia_tentato" and not os.path.exists(winhttp)
              and "riapplica" in e.actions and "crea_rapporto" in e.actions)


# --- games with translated files, Windows crashes ------------------------------------------------------

def test_file(base):
    game = os.path.join(base, "giochi", "Lantern Keeper")
    rel = fake_web_game.create(game)
    open(os.path.join(game, "Lantern Keeper.exe"), "wb").write(b"MZ")
    path, _ = automatic.prepare(game, "auto", "it", "google", silent)
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    write(project.memory_path(profile), json.dumps({
        "Start a new game": "Inizia una nuova partita", "Options and settings": "Opzioni e impostazioni",
        "Quit to the desktop": "Esci al desktop"}, ensure_ascii=False))
    automatic.reapply(game, silent)
    texts = os.path.join(game, *rel.split("/"))
    check("gioco web: tradotto, con la versione di PatchSmith nelle informazioni del file",
              "Inizia una nuova partita" in read_file(texts)
              and project.read_info(texts).get("patchsmith_versione") == __version__)
    problems = checks.check(path, apply=True, log=silent, events=[])
    check("gioco web appena tradotto: niente da fare", problems == [])
    # the info files with the name used up to 0.12 (.patchsmith-info.json) count as the last write
    old = os.path.join(base, "giochi", "vecchio")
    os.makedirs(old)
    write(os.path.join(old, "testi.json"), "{}")
    write(os.path.join(old, "testi.json" + project.OLD_INFO_SUFFIX),
           json.dumps({"hash_originale": "x", "hash_scritti": []}), time.time() - 3600)
    c = checks._Context(os.path.join(base, "vecchio.json"), {"cartella_gioco": old,
                                                                 "file": [{"percorso": "testi.json"}]}, {}, None, False,
                            None)
    check("ultima scrittura anche dalle informazioni con il nome vecchio (.patchsmith-info.json)",
              abs(checks.reference(c) - (time.time() - 3600)) < 5)
    # an update (or Steam's file verification) puts the original back
    shutil.copyfile(texts + project.BACKUP_SUFFIX, texts)
    check("file rimesso originale dal gioco: da controllare all'apertura",
              path in checks.to_check(events=[]))
    problems = checks.check(path, apply=True, log=silent, events=[])
    check("file rimesso originale dal gioco: traduzioni rimesse da sole (Riapplica)",
              codes(problems) == ["RIPRISTINATO_DAL_GIOCO"] and problems[0].state == "corretto"
              and "Inizia una nuova partita" in read_file(texts))
    # an update with new texts: the user is told to press "Analyze and translate", without touching anything
    data = json.loads(read_file(texts + project.BACKUP_SUFFIX))
    data["menu"]["nuovo"] = "A brand new menu entry"
    write(texts, json.dumps(data))
    whole = snapshot(game)
    problems = checks.check(path, apply=True, log=silent, events=[])
    e = checks.as_outcome(problems, path)
    check("file aggiornato dal gioco: 'Analizza e traduci' da fare, nulla cambiato",
              codes(problems) == ["AGGIORNATO"] and "traduci" in e.actions and snapshot(game) == whole)
    # crash in the Windows event log after the translation: to confirm, it is not restored automatically
    event = {"id": "999", "istante": time.time() + 5, "codice": 1000, "app": "Lantern Keeper.exe",
              "percorso": os.path.join(game, "Lantern Keeper.exe"), "testo": ""}
    other = dict(event, id="998", percorso=os.path.join(base, "altro", "Lantern Keeper.exe"))
    check("crash di Windows del gioco: da controllare all'apertura",
              path in checks.to_check(events=[event, other]))
    problems = checks.check(path, apply=True, log=silent, events=[event, other])
    crash = by_code(problems, "CRASH")
    check("crash di Windows: 'Ripristina originali' proposto, non fatto (un solo crash: l'altro e' di un'altra "
              "cartella)", crash and crash.state == "da_fare" and crash.action == "ripristina"
              and crash.signal == "eventi:999" and snapshot(game) == whole)
    check("crash di Windows gia' visto: non piu' da controllare all'apertura",
              path not in checks.to_check(events=[event, other]))

    # real events (reduced): 1000 with the executable's path, 1026 (.NET) with only the name, in the Windows
    # encoding
    events = checks.read_events(open(os.path.join(SIGNALS, "evento-crash-1000.xml"), "rb").read())
    check("evento 1000 vero: letto, del gioco giusto (non dell'altra copia)",
              len(events) == 1 and events[0]["app"] == "One Million Coins.exe"
              and len(checks.game_crash(OLD_COPY, events, 0)) == 1
              and not checks.game_crash(NEW_COPY, events, 0))
    events = checks.read_events(open(os.path.join(SIGNALS, "evento-crash-1026.xml"), "rb").read())
    with_exe = os.path.join(base, "giochi", "dotnet")
    os.makedirs(with_exe)
    open(os.path.join(with_exe, "EtwHostServiceUpdater.exe"), "wb").write(b"MZ")
    check("evento 1026 vero (codifica di Windows): il nome dell'eseguibile dal testo, riconosciuto nella cartella",
              len(events) == 1 and events[0]["app"] == "EtwHostServiceUpdater.exe"
              and len(checks.game_crash(with_exe, events, 0)) == 1
              and not checks.game_crash(game, events, 0))


def test_gamemaker(base):
    """Added language (local/italian.json): another language chosen in the game is only reported; if the game
    removed the language file, Reapply and then the language is put back (the game changed it, not the player)."""
    game = os.path.join(base, "giochi", "Forager")
    os.makedirs(os.path.join(game, "local"))
    original = os.path.join(game, "local", "english.json")
    write(original, json.dumps({"a": "Hello"}))
    new_value = os.path.join(game, "local", "italiano.json")
    write(new_value, json.dumps({"a": "Ciao"}))
    info = project.create_backup(original, {"solo_creati": True, "creati": ["italiano.json"]})
    project.save_info(original, info)
    path = os.path.join(automatic.profiles_folder(), "forager-prova.json")
    scan.save_json(path, {"nome": "forager-prova", "cartella_gioco": game.replace(os.sep, "/"),
                                    "motore": "gamemaker", "lingua_origine": "en", "lingua_destinazione": "it",
                                    "backend": "google",
                                    "file": [{"percorso": "local/english.json", "formato": "json",
                                              "lingua_nuova": "local/italiano.json"}]})
    official_language._save_log(game, {"tipo": "gamemaker_opzioni", "file": "opzioni", "scritto": "italiano",
                                             "precedente": "english"})
    options = os.path.join(base, "opzioni-forager.txt")
    write(options, "x")
    real_search, real_reapply = language_gamemaker.search, automatic.reapply
    real_set = official_language.set_added_language
    calls = []
    language_gamemaker.search = lambda _c, languages: (options, ("k", "m", 0, "english"))
    automatic.reapply = lambda folder, log, test=False, outcome=None: calls.append("riapplica") or \
        outcome.update(esito=outcome_mod.Outcome("OK", written=True, translated_items=1))
    official_language.set_added_language = lambda *a, **k: calls.append(("imposta", k.get("force")))
    try:
        problems = checks.check(path, apply=True, log=silent, events=[])
        check("GameMaker: un'altra lingua scelta nel gioco -> solo detto (la scelta di chi gioca resta)",
                  codes(problems) == ["LINGUA_AGGIUNTA"] and problems[0].state == "info" and not calls
                  and "Opzioni > Lingua" in problems[0].message)
        os.remove(new_value)
        problems = checks.check(path, apply=True, log=silent, events=[])
        check("GameMaker: lingua aggiunta tolta dal gioco -> Riapplica e lingua rimessa (forzata)",
                  codes(problems) == ["RIPRISTINATO_DAL_GIOCO"] and problems[0].state == "corretto"
                  and calls == ["riapplica", ("imposta", True)])
    finally:
        language_gamemaker.search, automatic.reapply = real_search, real_reapply
        official_language.set_added_language = real_set


outcome_mod = outcome


def test_unreal_xna(base):
    from patchsmith import adapter_unreal, adapter_xna
    game = os.path.join(base, "giochi", "Dungeon Defenders Awakened")
    pak = os.path.join(game, "DDS", "Content", "Paks", "zz_Patchsmith_it_999_P.pak")
    os.makedirs(os.path.dirname(pak))
    open(pak, "wb").write(b"pak finto")
    rel = "DDS/Content/Paks/zz_Patchsmith_it_999_P.pak"
    before = time.time() - 600
    write(os.path.join(game, adapter_unreal.MANIFEST), json.dumps({
        "versione": 1, "patchsmith": True, "lingua": "it", "profilo": "dda-prova",
        "file": {rel: hashlib.sha256(b"pak finto").hexdigest()}, "precedenti": {}, "sorgenti": {"x": "y"}}), before)
    path = os.path.join(automatic.profiles_folder(), "dda-prova.json")
    scan.save_json(path, {"nome": "dda-prova", "cartella_gioco": game.replace(os.sep, "/"),
                                    "motore": "unreal", "lingua_origine": "en", "lingua_destinazione": "it",
                                    "backend": "google", "file": []})
    log_file = os.path.join(base, "appdata", "DDS", "Saved", "Logs", "DDS.log")
    real = read_file(os.path.join(SIGNALS, "unreal-dds.log"))
    write(log_file, real.replace("configuration option (en)", "configuration option (fr)"))
    problems = checks.check(path, apply=False, log=silent, events=[])
    check("Unreal: il gioco usava un'altra lingua (dal suo registro) -> detto, pak caricato",
              codes(problems) == ["LINGUA_UNREAL"] and "'fr'" in problems[0].message)
    write(log_file, "\n".join(r for r in real.splitlines() if "zz_Patchsmith" not in r))
    problems = checks.check(path, apply=False, log=silent, events=[])
    check("Unreal: pak di PatchSmith non caricato -> rapporto", codes(problems) == ["PAK_NON_CARICATO"]
              and problems[0].action == "crea_rapporto")
    with zipfile.ZipFile(report.create(game, os.path.join(base, "rapporti-unreal"))) as z:
        names = z.namelist()
    check("Unreal: il rapporto proposto ha il registro del gioco (DDS.log) da cui il controllo l'ha capito",
              "unreal/DDS.log" in names and "gioco/patchsmith-unreal.json" in names)
    os.remove(pak)
    problems = checks.check(path, apply=False, log=silent, events=[])
    check("Unreal: pak tolto dal gioco -> si rimette (Riapplica)", "TRADUZIONE_TOLTA" in codes(problems)
              and by_code(problems, "TRADUZIONE_TOLTA").safe)

    xna = os.path.join(base, "giochi", "Rogue Legacy")
    os.makedirs(xna)
    open(os.path.join(xna, "RogueLegacy.exe"), "wb").write(b"MZ nuovo")
    write(os.path.join(xna, adapter_xna.MANIFEST), json.dumps({
        "versione": 1, "patchsmith": True, "lingua": "it", "profilo": "rl-prova",
        "file": {"it/RogueLegacy.resources.dll": "abc"}, "cartelle_create": ["it"],
        "sorgenti": {"RogueLegacy.exe": hashlib.sha256(b"MZ vecchio").hexdigest()}}))
    path = os.path.join(automatic.profiles_folder(), "rl-prova.json")
    scan.save_json(path, {"nome": "rl-prova", "cartella_gioco": xna.replace(os.sep, "/"), "motore": "xna",
                                    "lingua_origine": "en", "lingua_destinazione": "it", "backend": "google",
                                    "file": []})
    problems = checks.check(path, apply=False, log=silent, events=[])
    check("XNA: satellite tolto (si rimette) e gioco aggiornato (Analizza e traduci)",
              codes(problems) == ["AGGIORNATO", "TRADUZIONE_TOLTA"])


def test_after_verify(base):
    """Cases found in the verification of point 2: large logs, interrupted installation, game profile without folder,
    Unreal log with the project folder, Windows crashes already past, PatchSmith updated, command line with a broken
    game profile, live translator without translators."""
    import datetime
    from patchsmith import adapter_unreal
    name = "lunga-prova"
    game = os.path.join(base, "giochi", "Partita Lunga")
    create_unity_game(game, "Partita Lunga", "2022.3.10f1", "Studio Lungo", "Partita Lunga")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    before, after = time.time() - 600, time.time() - 120
    for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_PLUGIN, adapter_unity.SERVICE_CONFIG):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    # a long, successful session: "startup complete" at the start, then over 2 MB of warnings; this copy's
    # Player.log with the path at the start and the Google errors at the end
    warnings = "".join("[Warning: XUnity.AutoTranslator] Avviso di prova numero %d con un po' di testo\n" % i
                     for i in range(40000))
    write(os.path.join(game, *adapter_unity.BEPINEX_LOG.split("/")),
           read_file(os.path.join(SIGNALS, "bepinex-logoutput.log")) + warnings, after)
    lines = with_path(read_file(os.path.join(SIGNALS, "unity-player-errore-google.log")), NEW_COPY,
                         game).split("\n")
    write(os.path.join(base, "utente", "AppData", "LocalLow", "Studio Lungo", "Partita Lunga", "Player.log"),
           "\n".join(lines[:20]) + "\n" + warnings + "\n".join(lines[20:]), after)
    # the live translator started without translators (service log, shared by all games)
    current_time = datetime.datetime.fromtimestamp(after).strftime("%Y-%m-%d %H:%M:%S")
    write(os.path.join(base, "ollama", "servizio-xunity.log"),
           "%s Traduttore per XUnity in ascolto su http://127.0.0.1:1/translate (gioco: %s)\n"
           "%s Traduttori per le frasi nuove: nessuno (le frasi nuove restano in originale)\n"
           % (current_time, name, current_time))
    problems = checks.check(path, apply=False, log=silent, events=[])
    check("registri grandi (oltre 2 MB): una partita riuscita non e' un crash all'avvio, gli errori in fondo al "
              "Player.log di questa copia contano; traduttore dal vivo senza traduttori (%s)" % ", ".join(
                  codes(problems)), codes(problems) == ["SERVIZIO_REGISTRO", "TRADUTTORE_FERMO"])
    corrected = checks.Problem("TEMPORANEI", "info", "x", state="corretto")
    check("esito: '1 problema corretto' (singolare)",
              "1 problema corretto." in checks.as_outcome([corrected], path).reason)

    # PatchSmith updated: Unity games are checked again at startup (version to reconcile)
    other = os.path.join(automatic.profiles_folder(), "solo-crash-prova.json")
    real_version = checks.__version__
    before_sel = other in checks.to_check(events=[])
    checks.__version__ = "99.0.0"
    try:
        after_sel = other in checks.to_check(events=[])
    finally:
        checks.__version__ = real_version
    check("PatchSmith aggiornato: i giochi Unity gia' controllati tornano da controllare all'apertura",
              not before_sel and after_sel)

    # installation interrupted halfway: no automatic fix, Restore originals is proposed
    interrupted = os.path.join(base, "giochi", "Interrotta")
    create_unity_game(interrupted, "Interrotta", "2022.3.10f1", "Studio", "Interrotta")
    p_interrupted = unity_profile("interrotta-prova", interrupted)
    adapter_unity.install(interrupted, "en", "it", "interrotta-prova", silent, local=p_interrupted)
    m = json.loads(read_file(os.path.join(interrupted, adapter_unity.MANIFEST)))
    m["in_corso"] = True
    write(os.path.join(interrupted, adapter_unity.MANIFEST), json.dumps(m))
    os.remove(os.path.join(interrupted, "winhttp.dll"))
    whole = snapshot(interrupted)
    problems = checks.check(p_interrupted, apply=True, log=silent, events=[])
    check("installazione interrotta: niente correzioni da sole, 'Ripristina originali' proposto (%s)"
              % ", ".join(codes(problems)), codes(problems) == ["INSTALLAZIONE_INTERROTTA"]
              and problems[0].state == "da_fare" and problems[0].action == "ripristina"
              and snapshot(interrupted) == whole)

    # game profile without the game's folder: nothing to check (not the program's current folder)
    unlabeled = os.path.join(automatic.profiles_folder(), "senza-cartella.json")
    scan.save_json(unlabeled, {"nome": "senza-cartella", "motore": "gamemaker", "file": [{"percorso": "x.json"}]})
    lines = []
    problems = checks.check(unlabeled, apply=False, log=lines.append)
    check("profilo senza cartella: niente da controllare, senza usare la cartella attuale",
              problems == [] and not any(os.getcwd() in r for r in lines))
    os.remove(unlabeled)

    # Unreal: the game's log is found even if the chosen folder is the project's
    expected_list = [os.path.join(os.environ["LOCALAPPDATA"], "DDS", "Saved", "Logs", "DDS.log")]
    root = os.path.join(base, "giochi", "Dungeon Defenders Awakened")
    pak = "zz_Patchsmith_it_999_P.pak"
    check("Unreal: registro del gioco dalla cartella del gioco e da quella del progetto",
              checks._unreal_logs(root, {"file": {"DDS/Content/Paks/" + pak: "h"}}) == expected_list
              and checks._unreal_logs(os.path.join(root, "DDS"), {"file": {"Content/Paks/" + pak: "h"}})
              == expected_list and adapter_unreal.PAK_PREFIX in pak)

    # Windows crash from before the last check (not its own: e.g. from before a Reapply): it is not checked again at
    # every startup
    web = automatic.find_profile(os.path.join(base, "giochi", "Lantern Keeper"))
    since = checks.read_state(web).get("eventi_dal")
    old = {"id": "997", "istante": since - 30, "codice": 1000, "app": "Lantern Keeper.exe",
               "percorso": os.path.join(base, "giochi", "Lantern Keeper", "Lantern Keeper.exe"), "testo": ""}
    check("crash di Windows di prima dell'ultimo controllo: non da controllare all'apertura",
              web and since and web not in checks.to_check(events=[old]))

    # command line on all games with a broken game profile: the others are checked, code 1
    broken = os.path.join(automatic.profiles_folder(), "rotto-prova.json")
    scan.save_json(broken, {"nome": "rotto-prova", "cartella_gioco": game.replace(os.sep, "/"),
                                 "motore": "gamemaker", "file": [{"percorso": 5}]})
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", "--solo-leggi"], capture_output=True,
                       env=dict(os.environ), timeout=600)
    output = r.stdout.decode("utf-8", "replace")
    os.remove(broken)
    check("riga di comando con un profilo rovinato: errore detto, gli altri giochi controllati, codice 1",
              r.returncode == 1 and "ERRORE nel controllo di rotto-prova.json" in output
              and output.count("Controllo dopo aver giocato:") >= 6 and "Traceback" not in output
              and "Traceback" not in r.stderr.decode("utf-8", "replace"))


def test_retranslate_and_startup(base):
    """Point 5: at startup the journal's sentences are not re-translated (they remain to do, with the "Re-translate"
    button), the lost sentences are saved anyway; "does not accept mods" only with a real crash (not with just the
    incomplete LogOutput.log of a Unity 6 game that already starts at the first MonoBehaviour)."""
    name = "diario-rimandato-prova"
    game = os.path.join(base, "giochi", "Diario Rimandato")
    create_unity_game(game, "Diario Rimandato", "2022.3.10f1", "Studio Diario", "Diario Rimandato")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    before = time.time() - 600
    for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_PLUGIN, adapter_unity.SERVICE_CONFIG):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    memory = project.memory_path({"nome": name, "lingua_destinazione": "it"})
    write(memory, json.dumps({"Tea and maintenance bench": "Tavolo per il tè e per la manutenzione"}))
    write(xunity_service.journal_path(name), read_file(os.path.join(SIGNALS, "diario-richieste.jsonl")))
    cache = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    write(cache, "MASTER=MASTER\nWarm pool of light=Warm piscina di luce\n")
    calls, real = [], checks.create_backend
    checks.create_backend = lambda name_b, log=print: calls.append(name_b) or DictTranslator()
    try:
        problems = checks.check(path, apply=True, log=silent, events=[], retranslate=False)
        postponed = by_code(problems, "FRASI_DA_RITRADURRE")
        lost = by_code(problems, "FRASI_DA_RIFARE")
        saved = json.loads(read_file(memory))
        check("all'apertura: frasi provvisorie da fare con 'Ritraduci' (5), nessun traduttore usato (%s)"
                  % ", ".join("%s=%s" % (p.code, p.state) for p in problems),
                  postponed and postponed.state == "da_fare" and postponed.action == "ritraduci"
                  and postponed.extra.get("frasi") == 5 and calls == []
                  and adapter_unity.xunity_cache_entries(game, "it").get("MASTER") == "MASTER")
        check("all'apertura: le frasi tradotte ma perse si salvano lo stesso (senza traduttore)",
                  lost and lost.state == "corretto" and saved.get("Continuous glove") == "Guanto a lunga durata")
        state = checks.read_state(path)
        e = checks.as_outcome(problems, path)
        stored = checks.saved_outcome(path)
        window = outcome.window(e, "controlla")
        check("all'apertura: il diario non va avanti; esito con 'ritraduci' (anche quello salvato, con 5 frasi)",
                  not state.get("diario_fino") and "ritraduci" in e.actions and stored is not None
                  and any(p.get("frasi") == 5 and p.get("azione") == "ritraduci" for p in stored.extra["controllo"])
                  and ("Ritraduci le frasi", "ritraduci") in window["pulsanti"])
        problems = checks.check(path, apply=True, log=silent, events=[], retranslate=True)
        redone = by_code(problems, "FRASI_DA_RIFARE")
        check("pulsante 'Ritraduci': le frasi si ritraducono e vanno nella cache, il diario va avanti (%s)"
                  % ", ".join("%s=%s" % (p.code, p.state) for p in problems),
                  redone and redone.state == "corretto" and calls
                  and adapter_unity.xunity_cache_entries(game, "it").get("MASTER") == "GENERALE"
                  and checks.read_state(path).get("diario_fino") == "2026-10-18T04:30:29"
                  and not by_code(problems, "FRASI_DA_RITRADURRE"))
    finally:
        checks.create_backend = real

    # Unity 6, already with the MonoBehaviour entry point: incomplete LogOutput.log without a crash = only for info
    name = "avvio-lento-prova"
    game = os.path.join(base, "giochi", "Avvio Lento")
    create_unity_game(game, "Avvio Lento", "6000.3.12f1", "Studio Sei", "Avvio Lento")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    before, after = time.time() - 600, time.time() - 120
    for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_PLUGIN, adapter_unity.SERVICE_CONFIG):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    write(os.path.join(game, *adapter_unity.BEPINEX_LOG.split("/")),
           "[Message:   BepInEx] BepInEx 5.4.23.2 - Avvio Lento\n[Info   :   BepInEx] Running under Unity "
           "v6000.3.12f1\n[Message:   BepInEx] Preloader started\n", after)
    problems = checks.check(path, apply=False, log=silent, events=[])
    incomplete = by_code(problems, "AVVIO_INCOMPLETO")
    check("Unity 6 con MonoBehaviour, LogOutput.log incompleto e nessun crash: solo da sapere, niente 'non accetta "
              "mod' (%s)" % ", ".join(codes(problems)), adapter_unity.current_entrypoint(game) == "MonoBehaviour"
              and incomplete and incomplete.state == "info" and not by_code(problems, "CRASH_CON_MOD"))
    event = {"id": "5001", "istante": after + 1, "codice": 1000, "app": "Avvio Lento.exe",
              "percorso": os.path.join(game, "Avvio Lento.exe"), "testo": ""}
    problems = checks.check(path, apply=False, log=silent, events=[event])
    mod = by_code(problems, "CRASH_CON_MOD")
    check("...con un crash vero (registro di Windows): 'non accetta mod' da confermare, senza 'anche dopo la "
              "correzione' (non c'e' stata)", mod and not mod.safe and mod.action == "ripristina"
              and "anche dopo" not in mod.message)
    checks._save_state(path, {"entrypoint_forzato": True})
    problems = checks.check(path, apply=False, log=silent, events=[event])
    mod = by_code(problems, "CRASH_CON_MOD")
    check("...e dopo la correzione del punto d'ingresso: 'anche dopo la correzione'",
              mod and "anche dopo la correzione" in mod.message)


def test_pending_fixes(base):
    """Revision 0.16: the fixes from "Fix translations" saved with the Unity game open (XUnity cache lines pending in
    .to_write.json) are written by the check, with the game closed, once; a line changed by hand after the fix
    stays. Installed with a previous version and configuration to put back: a single message (CONFIGURAZIONE that
    states the version), not VERSION as well."""
    from patchsmith import corrections
    name = "correzioni-in-attesa"
    game = os.path.join(base, "giochi", "Correzioni In Attesa")
    create_unity_game(game, "Attesa", "2022.3.10f1", "Studio Attesa", "Correzioni In Attesa")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    profile = json.loads(read_file(path))
    adapter_unity.write_xunity_cache(game, "it", {"Start a new game": "Inizia nuovo gioco",
                                                    "Load game": "Carica partita", "Quit": "Esci"}, silent)
    checks.check(path, apply=True, log=silent)  # (the starting state: nothing to do)
    check("correzioni in attesa: nessuna, nessun problema",
              "CORREZIONI_IN_ATTESA" not in codes(checks.check(path, apply=False, log=silent)))
    # two fixes saved with the game open; then one of the two lines changed by hand in the file
    corrections.mark_to_write(profile, {"Start a new game": ("Inizia nuovo gioco", "Nuova partita"),
                                           "Load game": ("Carica partita", "Carica una partita")})
    cache = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    write(cache, read_file(cache).replace("Load game=Carica partita", "Load game=Carica (a mano)"))
    check("correzioni in attesa: il controllo all'apertura le vede (segnali cambiati)",
              path in checks.to_check(events=[]))
    whole = snapshot(base)
    problems = checks.check(path, apply=False, log=silent)
    p = by_code(problems, "CORREZIONI_IN_ATTESA")
    check("correzioni in attesa: in sola lettura 'si correggerebbe da solo', con un messaggio per chi gioca, "
              "nulla scritto", p and p.state == "da_correggere" and "2 tue correzioni" in p.message
              and "Correggi traduzioni" in p.message and snapshot(base) == whole)
    real = system.game_open
    system.game_open = lambda _c: True
    try:
        problems = checks.check(path, apply=True, log=silent)
    finally:
        system.game_open = real
    check("correzioni in attesa con il gioco aperto: rimandate, cache non toccata",
              by_code(problems, "CORREZIONI_IN_ATTESA").state == "rimandato" and snapshot(base) == whole)
    problems = checks.check(path, apply=True, log=silent)
    entries = adapter_unity.xunity_cache_entries(game, "it")
    p = by_code(problems, "CORREZIONI_IN_ATTESA")
    check("correzioni in attesa a gioco chiuso: scritta quella con la riga com'era, quella cambiata a mano resta, "
              "le altre righe identiche, niente piu' in attesa (%s)" % (p.detail if p else None),
              p and p.state == "corretto" and entries.get("Start a new game") == "Nuova partita"
              and entries.get("Load game") == "Carica (a mano)" and entries.get("Quit") == "Esci"
              and "una lasciata" in p.detail and not corrections.to_write(profile))
    check("correzioni in attesa: il controllo dopo non trova piu' nulla",
              "CORREZIONI_IN_ATTESA" not in codes(checks.check(path, apply=True, log=silent)))
    # installed with a previous version and with the configuration to put back: a single message
    m = json.loads(read_file(os.path.join(game, adapter_unity.MANIFEST)))
    m["patchsmith_versione"] = "0.15.0"
    write(os.path.join(game, adapter_unity.MANIFEST), json.dumps(m))
    cfg = os.path.join(game, *adapter_unity.CONFIG_BEPINEX.split("/"))
    write(cfg, read_file(cfg).replace("WriteUnityLog = true", "WriteUnityLog = false"))
    problems = checks.check(path, apply=False, log=silent)
    p = by_code(problems, "CONFIGURAZIONE")
    check("versione precedente e configurazione da rimettere: solo CONFIGURAZIONE, che dice la versione (%s)"
              % ", ".join(codes(problems)), p and "VERSIONE" not in codes(problems)
              and "un'altra versione di PatchSmith (0.15.0)" in p.message)
    checks.check(path, apply=True, log=silent)
    check("... dopo la correzione: versione nel manifesto aggiornata, niente da fare",
              adapter_unity.read_manifest(game).get("patchsmith_versione") == __version__
              and checks.check(path, apply=True, log=silent) == [])
    # the report has the pending fixes and the letters of the fonts read (Unity Localization: font.json)
    corrections.mark_to_write(profile, {"Quit": ("Esci", "Esci dal gioco")})
    font = project.data_folder("memory", "%s.unity" % name, "font.json")
    write(font, json.dumps({"descrizione": "prova"}))
    zip_ = report.create(game, os.path.join(base, "rapporti-attesa"))
    with zipfile.ZipFile(zip_) as z:
        names = z.namelist()
    check("rapporto: le correzioni non ancora scritte nel gioco e le lettere dei font",
              "traduzioni/%s.it.da_scrivere.json" % name in names and "traduzioni/%s.font.json" % name in names)
    os.remove(corrections.to_write_path(profile))
    os.remove(font)


def test_retranslate_without_translator(base):
    """Revision 0.16: "Re-translate" with the local translator chosen but not installed: the lost sentences are saved
    anyway and the button to fix it is "Install the local translator" (before, only "Analyze and translate")."""
    name = "ritraduci-senza-traduttore"
    game = os.path.join(base, "giochi", "Senza Traduttore")
    create_unity_game(game, "Senza Traduttore", "2022.3.10f1", "Studio Senza", "Senza Traduttore")
    path = unity_profile(name, game)
    adapter_unity.install(game, "en", "it", name, silent, local=path)
    before = time.time() - 600
    for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_PLUGIN, adapter_unity.SERVICE_CONFIG):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    write(xunity_service.journal_path(name), read_file(os.path.join(SIGNALS, "diario-richieste.jsonl")))

    def absent(_name, log=print):
        raise backend.BackendError(backend.NOT_INSTALLED, "TRADUTTORE_ASSENTE", command=backend.INSTALL_COMMAND)

    real = checks.create_backend
    checks.create_backend = absent
    try:
        problems = checks.check(path, apply=True, log=silent, events=[], retranslate=True, translator="locale")
    finally:
        checks.create_backend = real
    p = by_code(problems, "FRASI_DA_RIFARE")
    memory = json.loads(read_file(project.memory_path({"nome": name, "lingua_destinazione": "it"})))
    window = outcome.window(checks.as_outcome(problems, path), "controlla")
    check("'Ritraduci' senza il traduttore locale: frasi perse salvate, 'Installa il traduttore locale' nella "
              "finestra, niente 'smith.py' (%s)" % (p.detail if p else None),
              p and p.state == "non_riuscito" and p.action == "installa_locale"
              and memory.get("Continuous glove") == "Guanto a lunga durata"
              and ("Installa il traduttore locale", "installa_locale") in window["pulsanti"]
              and "smith.py" not in window["testo"])


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    for env_var, name in (("TEMP", "temp"), ("TMP", "temp"), ("USERPROFILE", "utente"),
                            ("LOCALAPPDATA", "appdata"), ("APPDATA", "appdata-roaming")):
        os.environ[env_var] = os.path.join(base, name)
        os.makedirs(os.environ[env_var], exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "dati")
    os.environ["PATCHSMITH_DATI"] = project.DATA_ROOT  # also for smith.py launched by the tests
    automatic.PROFILES_FOLDER = None
    ollama.folder = lambda: os.path.join(base, "ollama")  # no local translator, test log
    adapter_unity.is_running = lambda *_: False
    adapter_unity._compile_plugin = _fake_plugin
    checks.create_backend = lambda name, log=print: DictTranslator()
    prepare_components(os.path.join(base, "componenti"))
    test_unity(base)
    test_crash_only(base)
    test_old_version(base)
    test_file(base)
    test_gamemaker(base)
    test_unreal_xna(base)
    # all the test games together, read-only: no errors, something to do (code 2), nothing written
    whole = snapshot(base)
    r = subprocess.run([sys.executable, "-I", SMITH, "controlla", "--solo-leggi"], capture_output=True,
                       env=dict(os.environ), timeout=600)
    output = r.stdout.decode("utf-8", "replace")
    check("riga di comando: 'smith.py controlla --solo-leggi' su tutti i giochi -> codice 2, nessun errore, nulla "
              "scritto", r.returncode == 2 and "ERRORE" not in output and output.count("Controllo dopo aver giocato:")
              >= 6 and snapshot(base) == whole)
    test_after_verify(base)
    test_retranslate_and_startup(base)
    test_pending_fixes(base)
    test_retranslate_without_translator(base)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
