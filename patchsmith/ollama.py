"""Traduttore locale: Ollama (https://ollama.com, licenza MIT) con i modelli TranslateGemma (Gemma Terms of Use).

Tutto sta nella cartella di PatchSmith (components/ollama: programma e modelli), nessuna installazione nel sistema:
- il server si avvia solo durante una traduzione, su una porta locale tutta sua (127.0.0.1, 11435-11444), e si
  chiude alla fine; un "Job Object" di Windows lo chiude comunque se PatchSmith si interrompe di colpo;
- all'ambiente del server si tolgono le variabili OLLAMA_* dell'utente e si mettono quelle di PatchSmith
  (modelli su D:, nessuna funzione cloud);
- le chiamate al server locale non passano mai da un proxy di sistema.
Su C: Ollama scrive solo una piccola chiave in %USERPROFILE%\\.ollama.
"""
import ctypes
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from . import interruption as interruzione

VERSIONE = "0.40.4"
URL_ZIP = "https://github.com/ollama/ollama/releases/download/v0.40.4/ollama-windows-amd64.zip"
DIMENSIONE_ZIP = 1469916992
SHA256_ZIP = "7485a1172f8527db4342a63521b9342fc466185ff0855da378cc864ecf6354cf"
MODELLI = {
    "4b": {"nome": "translategemma:4b-it-q4_K_M", "impronta": "c49d986b0764", "gb": 3.3,
           "etichetta": "Veloce (TranslateGemma 4B)", "secondi_per_testo": 0.25, "secondi_avvio": 30,
           "parallele": 4},  # prova su RTX 3060 Ti: 4 righe insieme 0,10 s a riga contro 0,14
    "12b": {"nome": "translategemma:12b-it-q4_K_M", "impronta": "c2f9a9ca1ec7", "gb": 8.1,
            "etichetta": "Qualità (TranslateGemma 12B)", "secondi_per_testo": 2.5, "secondi_avvio": 80},
}
LICENZA_MODELLI = "https://ai.google.dev/gemma/terms"
_SENZA_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ErroreLocale(Exception):
    """Il traduttore locale non e' installato, non parte o non ha memoria a sufficienza."""


def cartella():
    from . import project as progetto
    return progetto.cartella_componenti("ollama")


def programma():
    return os.path.join(cartella(), VERSIONE, "ollama.exe")


def cartella_modelli():
    return os.path.join(cartella(), "modelli")


def installato():
    return os.path.isfile(programma())


def modello_presente(chiave):
    """True se il modello e' gia' scaricato (si guarda il suo manifesto, senza avviare il server)."""
    nome, tag = MODELLI[chiave]["nome"].split(":")
    return os.path.isfile(os.path.join(cartella_modelli(), "manifests", "registry.ollama.ai", "library", nome, tag))


SOGLIA_12B = 7.5  # GB di memoria della scheda video da cui si consiglia il 12B (le schede da 8 GB ne dichiarano ~7,9)
_MEMORIA_VIDEO = []  # la memoria video gia' cercata (una volta per avvio di PatchSmith)


def _programma_nvidia_smi():
    """nvidia-smi.exe del driver NVIDIA (System32, la cartella NVSMI dei driver vecchi, poi il PATH), o None. Mai dalla
    cartella corrente: un programma con quel nome messo li' (per esempio nella cartella di un gioco scaricato) non
    deve partire. Fuori da Windows: quello nel PATH."""
    if not sys.platform.startswith("win"):
        return shutil.which("nvidia-smi")
    candidati = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NVIDIA Corporation", "NVSMI",
                              "nvidia-smi.exe")]
    candidati += [os.path.join(d.strip().strip('"'), "nvidia-smi.exe")
                  for d in os.environ.get("PATH", "").split(os.pathsep) if os.path.isabs(d.strip().strip('"'))]
    corrente = os.path.normcase(os.path.abspath(os.getcwd()))
    return next((p for p in candidati if os.path.isfile(p)
                 and os.path.normcase(os.path.dirname(os.path.abspath(p))) != corrente), None)


def _nvidia_smi():
    """GB della scheda NVIDIA piu' grande, da nvidia-smi (arriva con il driver NVIDIA), o None."""
    programma = _programma_nvidia_smi()
    if not programma:
        return None
    try:
        r = subprocess.run([programma, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, timeout=10, creationflags=0x08000000 if sys.platform.startswith("win")
                           else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    valori = [int(x) for x in r.stdout.decode("ascii", "replace").split() if x.isdigit()]  # MiB
    return max(valori) / 1024 if r.returncode == 0 and valori else None


def _registro_video():
    """GB della scheda video piu' grande secondo il suo driver (registro di Windows, sola lettura), o None."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
        classe = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}")
    except OSError:
        return None
    valori, i = [], 0
    with classe:
        while True:
            try:
                nome = winreg.EnumKey(classe, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(classe, nome) as scheda:
                    valore, _ = winreg.QueryValueEx(scheda, "HardwareInformation.qwMemorySize")
            except OSError:
                continue
            if isinstance(valore, bytes):
                valore = int.from_bytes(valore[:8], "little")
            if isinstance(valore, int) and valore > 0:
                valori.append(valore / 1024 ** 3)
    return max(valori) if valori else None


def _wmi_video():
    """GB della scheda video piu' grande da WMI (PowerShell, sola lettura), o None. WMI arriva al massimo a 4 GB:
    un valore da 4 GB vuol dire "almeno 4"."""
    if not sys.platform.startswith("win"):
        return None
    # il percorso intero: con il solo nome Windows cercherebbe prima nella cartella corrente
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")
    if not os.path.isfile(powershell):
        return None
    try:
        r = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-CimInstance Win32_VideoController).AdapterRAM"], capture_output=True, timeout=20,
                           creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return None
    valori = [int(x) for x in r.stdout.decode("ascii", "replace").split() if x.isdigit()]
    if not valori or max(valori) >= 0xF0000000:  # al limite dei 4 GB: la memoria vera non si sa
        return None
    return max(valori) / 1024 ** 3


def memoria_video():
    """GB di memoria della scheda video (la piu' grande), letti senza installare ne' scaricare nulla: nvidia-smi se
    c'e' (driver NVIDIA), poi il registro di Windows (il valore scritto dal driver), poi WMI con PowerShell. None se
    non si sa. Si cerca una volta sola."""
    if not _MEMORIA_VIDEO:
        valore = None
        for cerca in (_nvidia_smi, _registro_video, _wmi_video):
            try:
                valore = cerca()
            except Exception:  # noqa: BLE001 - solo un consiglio: un errore qui non deve fermare nulla
                valore = None
            if valore:
                break
        _MEMORIA_VIDEO.append(valore)
    return _MEMORIA_VIDEO[0]


def modello_consigliato(gb=None):
    """(chiave del modello, motivo per chi gioca): il 12B (sbaglia meno, ma e' piu' lento) se la scheda video ha
    almeno SOGLIA_12B GB di memoria, altrimenti il 4B (il 12B fuori dalla scheda video diventa molto lento)."""
    gb = memoria_video() if gb is None else gb
    if gb is None:
        return "4b", ("Non riesco a sapere quanta memoria ha la tua scheda video: ti propongo il modello Veloce (4B), "
                      "che va bene su quasi tutti i PC.")
    if gb >= SOGLIA_12B:
        return "12b", ("La tua scheda video ha %s GB di memoria: ti propongo il modello Qualità (12B), che sbaglia meno "
                       "(è più lento del Veloce)." % _gb(gb))
    return "4b", ("La tua scheda video ha %s GB di memoria: ti propongo il modello Veloce (4B). Il Qualità (12B) ne "
                  "vuole almeno 8 e senza diventerebbe molto lento." % _gb(gb))


SOGLIA_SCHEDA = 3.5  # GB: sotto (grafica integrata o scheda vecchia) il modello gira quasi tutto sul processore
RAM_MINIMA = 8  # GB di memoria del PC: sotto, anche il 4B (circa 4 GB) lascia poco al resto di Windows


def memoria_pc():
    """GB di memoria (RAM) del PC, o None se non si sa."""
    if not sys.platform.startswith("win"):
        return None
    import ctypes

    class _Stato(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    stato = _Stato()
    stato.dwLength = ctypes.sizeof(_Stato)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stato)):
            return stato.ullTotalPhys / 1024 ** 3
    except (OSError, AttributeError):
        pass
    return None


def avviso_prestazioni(gb_video=None, gb_ram=None):
    """Testo da mostrare PRIMA di scaricare se questo PC fara' andare piano il traduttore locale ('' se va bene o non
    si sa): senza una scheda video adatta (grafica integrata, schede vecchie o con poca memoria) il modello gira sul
    processore, molte volte piu' lento; con poca RAM anche il resto del PC rallenta."""
    gb_video = memoria_video() if gb_video is None else gb_video
    gb_ram = memoria_pc() if gb_ram is None else gb_ram
    avvisi = []
    if gb_video is not None and gb_video < SOGLIA_SCHEDA:
        avvisi.append("La scheda video di questo PC ha %s GB di memoria (o è la grafica integrata nel processore): il "
                      "traduttore locale userà soprattutto il processore e sarà molto più lento, anche 10-20 volte. "
                      "Un gioco con 2.000 frasi può richiedere un'ora o più invece di pochi minuti." % _gb(gb_video))
    if gb_ram is not None and gb_ram < RAM_MINIMA:
        avvisi.append("Il PC ha %s GB di memoria (RAM): mentre traduce il modello ne usa circa 4, e il resto del PC "
                      "può rallentare." % _gb(gb_ram))
    if not avvisi:
        return ""
    return " ".join(avvisi) + (" Funziona lo stesso; in alternativa puoi usare DeepL con la tua chiave gratuita "
                               "(500.000 caratteri al mese, nessun programma da installare).")


def _gb(valore):
    return ("%.1f" % valore).replace(".", ",").replace(",0", "")


def modello_predefinito():
    """Il migliore gia' scaricato. Prove su Forager (RTX 3060 Ti 8 GB, ottobre 2026): il 12B sbaglia meno nomi e
    meno grammatica ma e' circa 5 volte piu' lento (2,5 s a testo contro 0,5); si scarica solo se l'utente lo
    sceglie, quindi se c'e' e' voluto."""
    return next((k for k in ("12b", "4b") if modello_presente(k)), "4b")


def stato():
    """'pronto', 'manca il modello' o 'non installato' (per l'interfaccia)."""
    if not installato():
        return "non installato"
    presenti = [k for k in MODELLI if modello_presente(k)]
    return "pronto" if presenti else "manca il modello"


# --- Job Object: il server muore con PatchSmith ----------------------------------------------------

class _BASE(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RB", "WB", "OB")]


class _ESTESA(ctypes.Structure):
    _fields_ = [("Base", _BASE), ("Io", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _lega_al_job(pid):
    """Mette il processo in un Job Object che si chiude (con tutti i figli) quando PatchSmith termina."""
    if not sys.platform.startswith("win"):
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.OpenProcess.restype = ctypes.c_void_p
        job = k32.CreateJobObjectW(None, None)
        info = _ESTESA()
        info.Base.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        processo = k32.OpenProcess(0x0101, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if not processo:
            return None
        ok = k32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(processo))
        k32.CloseHandle(ctypes.c_void_p(processo))
        return job if ok else None
    except (OSError, AttributeError):
        return None


# --- server ---------------------------------------------------------------------------------------

def _porta_occupata(porta):
    """True se qualcuno risponde gia' su questa porta (anche in ascolto su 0.0.0.0 o [::])."""
    for famiglia, indirizzo in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(famiglia, socket.SOCK_STREAM) as c:
                c.settimeout(0.2)
                if c.connect_ex((indirizzo, porta)) == 0:
                    return True
        except OSError:
            continue
    return False


def _porta_libera():
    for porta in range(11435, 11445):
        if _porta_occupata(porta):
            continue
        s = socket.socket()
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", porta))
            return porta
        except OSError:
            continue
        finally:
            s.close()
    raise ErroreLocale("Nessuna porta libera per il traduttore locale (11435-11444).")


_LIMITE_LOG = 5 * 1024 * 1024


class Server:
    """Il server Ollama di PatchSmith: si avvia con avvia(), si chiude con chiudi() (o con 'with')."""

    def __init__(self, log=print, eseguibile=None, attesa_avvio=180.0, comando=None, parallele=1):
        self.log = log
        self.eseguibile = eseguibile or programma()
        self.comando = comando or [self.eseguibile]  # i test usano un Ollama simulato
        self.attesa_avvio = attesa_avvio
        self.parallele = max(1, int(parallele))  # richieste elaborate insieme dalla scheda video
        self.processo = None
        self.porta = None
        self._job = None
        self._file_log = None
        self._inizio_log = 0  # posizione nel registro all'avvio: gli errori mostrano solo le righe di questa volta

    def __enter__(self):
        try:
            self.avvia()
        except BaseException:  # Ferma o errore durante l'avvio: il server appena lanciato non resta acceso
            self.chiudi()
            raise
        return self

    def __exit__(self, *_):
        self.chiudi()

    @property
    def attivo(self):
        return self.processo is not None and self.processo.poll() is None

    def avvia(self):
        if self.attivo:
            return
        if not os.path.isfile(self.comando[0]):
            raise ErroreLocale("Il traduttore locale non e' installato: scegli 'Traduttore locale' e premi "
                               "'Installa'.")
        os.makedirs(cartella_modelli(), exist_ok=True)
        temporanea = os.path.join(cartella(), "tmp")
        os.makedirs(temporanea, exist_ok=True)
        self.porta = _porta_libera()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("OLLAMA_") and k.upper() != "HTTP_PROXY"}
        env.update(OLLAMA_HOST="127.0.0.1:%d" % self.porta, OLLAMA_MODELS=cartella_modelli(), OLLAMA_NO_CLOUD="1",
                   OLLAMA_KEEP_ALIVE="30m", OLLAMA_NUM_PARALLEL=str(self.parallele), OLLAMA_MAX_LOADED_MODELS="1",
                   OLLAMA_CONTEXT_LENGTH="2048", OLLAMA_NOPRUNE="1", TEMP=temporanea, TMP=temporanea)
        if not any(k.upper() == "HTTPS_PROXY" for k in env):
            # proxy impostato solo in Windows (registro): Ollama, scritto in Go, legge solo le variabili
            proxy = urllib.request.getproxies().get("https")
            if proxy:
                env["HTTPS_PROXY"] = proxy
        env["NO_PROXY"] = ",".join(x for x in (env.pop("NO_PROXY", ""), "127.0.0.1,localhost") if x)
        registro = os.path.join(cartella(), "server.log")
        try:
            if os.path.getsize(registro) > _LIMITE_LOG:
                os.replace(registro, registro + ".1")
        except OSError:
            pass
        self._file_log = open(registro, "ab")
        self._inizio_log = self._file_log.tell()
        self.processo = subprocess.Popen(
            self.comando + ["serve"], env=env, cwd=os.path.dirname(self.comando[0]), stdin=subprocess.DEVNULL,
            stdout=self._file_log, stderr=subprocess.STDOUT,
            creationflags=(0x08000000 | 0x00000200) if sys.platform.startswith("win") else 0)
        try:
            self._job = _lega_al_job(self.processo.pid)
            limite = time.monotonic() + self.attesa_avvio
            while time.monotonic() < limite:
                interruzione.controlla()
                if self.processo.poll() is not None:
                    raise ErroreLocale("Il traduttore locale non si e' avviato:\n%s" % _coda_log(self._inizio_log))
                try:
                    versione = self.chiama("/api/version", timeout=2).get("version")
                except (OSError, ValueError, AttributeError):
                    time.sleep(0.25)
                    continue
                # risponde il nostro server (non un altro programma sulla stessa porta)?
                if versione == VERSIONE and self.processo.poll() is None:
                    return
                time.sleep(0.25)
            raise ErroreLocale("Il traduttore locale non risponde dopo %d secondi:\n%s"
                               % (self.attesa_avvio, _coda_log(self._inizio_log)))
        except BaseException:  # errore, Ferma o Ctrl+C durante l'avvio: niente server lasciato acceso
            self.chiudi()
            raise

    def chiama(self, percorso, dati=None, timeout=180):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.porta, percorso),
                                     data=json.dumps(dati).encode("utf-8") if dati is not None else None,
                                     headers={"Content-Type": "application/json"})
        with _SENZA_PROXY.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def flusso(self, percorso, dati, timeout=3600):
        """Righe NDJSON di una risposta in streaming (es. /api/pull)."""
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.porta, percorso),
                                     data=json.dumps(dati).encode("utf-8"), headers={"Content-Type": "application/json"})
        with _SENZA_PROXY.open(req, timeout=timeout) as r:
            for riga in r:
                interruzione.controlla()
                if riga.strip():
                    yield json.loads(riga.decode("utf-8"))

    def chiudi(self):
        if self.processo is not None:
            if self.processo.poll() is None:
                if sys.platform.startswith("win"):
                    subprocess.run(["taskkill", "/PID", str(self.processo.pid), "/T", "/F"], capture_output=True,
                                   creationflags=0x08000000)
                else:
                    self.processo.kill()
                try:
                    self.processo.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            self.processo = None
        if self._job:
            try:
                ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(self._job))
            except (OSError, AttributeError):
                pass
            self._job = None
        if self._file_log:
            self._file_log.close()
            self._file_log = None


def _coda_log(dalla=0, righe=15):
    """Le ultime righe del registro scritte da 'dalla' in poi (al massimo gli ultimi 64 KB)."""
    try:
        with open(os.path.join(cartella(), "server.log"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(dalla, f.tell() - 65536))
            testo = f.read().decode("utf-8", "replace").splitlines()[-righe:]
        return "\n".join(testo) or "(il server non ha scritto nulla)"
    except OSError:
        return "(registro del server non disponibile)"


# --- installazione (solo con il consenso dell'utente) ---------------------------------------------------

def _sha256(percorso):
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def descrizione_download(scelti):
    """Testo da mostrare PRIMA di scaricare (file, fonte, dimensione, licenza)."""
    righe = []
    if not installato():
        righe.append("Ollama %s: ollama-windows-amd64.zip, %.2f GB, da github.com/ollama/ollama (release ufficiale, "
                     "licenza MIT, impronta SHA-256 controllata)" % (VERSIONE, DIMENSIONE_ZIP / 1e9))
    for k in scelti:
        if not modello_presente(k):
            righe.append("%s: %s, %.1f GB, dal registro ufficiale di Ollama (ollama.com). Licenza Gemma Terms of "
                         "Use (%s): scaricandolo la accetti" % (MODELLI[k]["etichetta"], MODELLI[k]["nome"],
                                                                MODELLI[k]["gb"], LICENZA_MODELLI))
    return righe


def installa(scelti, log=print):
    """Scarica (se manca) e installa Ollama e i modelli scelti in componenti/ollama. Da chiamare solo dopo il
    consenso dell'utente. Si puo' fermare: gli scaricamenti ripartono da dove erano."""
    servono = (0 if installato() else DIMENSIONE_ZIP * 3.5) + sum(MODELLI[k]["gb"] * 1e9 for k in scelti
                                                                  if not modello_presente(k))
    os.makedirs(cartella(), exist_ok=True)
    if shutil.disk_usage(cartella()).free < servono + 1e9:
        raise ErroreLocale("Spazio insufficiente: servono circa %.0f GB liberi su %s." % ((servono + 1e9) / 1e9,
                                                                                       os.path.splitdrive(cartella())[0]))
    try:
        if not installato():
            _installa_programma(log)
        _scarica_modelli(scelti, log)
    except (OSError, ValueError) as e:  # rete, disco, risposta inattesa: messaggio comprensibile, non un traceback
        raise ErroreLocale("Installazione non riuscita (%s: %s). Controlla la connessione e riprova: gli "
                           "scaricamenti riprendono da dove erano." % (type(e).__name__, e)) from None
    return True


def _scarica_modelli(scelti, log):
    with Server(log) as server:
        for k in scelti:
            if modello_presente(k):
                log("  %s: gia' scaricato" % MODELLI[k]["etichetta"])
                continue
            log("  Scarico %s (%.1f GB)..." % (MODELLI[k]["etichetta"], MODELLI[k]["gb"]))
            parti, ultimo = {}, -10.0
            for stato_pull in server.flusso("/api/pull", {"model": MODELLI[k]["nome"], "stream": True}):
                if "error" in stato_pull:
                    raise ErroreLocale("Scaricamento del modello non riuscito: %s" % stato_pull["error"])
                if stato_pull.get("digest") and stato_pull.get("total"):
                    parti[stato_pull["digest"]] = (stato_pull.get("completed", 0), stato_pull["total"])
                    fatto = 100 * sum(c for c, _ in parti.values()) / max(1, sum(t for _, t in parti.values()))
                    if fatto >= ultimo + 10:
                        ultimo = fatto
                        log("    %.0f%%" % fatto)
            presenti = {m.get("name"): m.get("digest", "") for m in server.chiama("/api/tags").get("models", [])}
            if not presenti.get(MODELLI[k]["nome"], "").startswith(MODELLI[k]["impronta"]):
                log("  ! %s: impronta diversa da quella attesa (il modello e' stato aggiornato?)" % MODELLI[k]["nome"])
            log("  %s pronto." % MODELLI[k]["etichetta"])


def _installa_programma(log):
    zip_ = os.path.join(os.path.dirname(cartella()), "ollama-windows-amd64-%s.zip" % VERSIONE)
    if not (os.path.exists(zip_) and os.path.getsize(zip_) == DIMENSIONE_ZIP and _sha256(zip_) == SHA256_ZIP):
        parziale = zip_ + ".scaricamento"
        log("  Scarico Ollama %s (%.2f GB) da github.com..." % (VERSIONE, DIMENSIONE_ZIP / 1e9))
        for tentativo in range(3):
            try:
                _scarica_zip(parziale, log)
                break
            except ErroreLocale:
                raise
            except OSError as e:  # rete: il pezzo gia' scaricato resta, si riprende da li'
                if tentativo == 2:
                    raise
                log("    connessione interrotta (%s): riprovo tra %d secondi..." % (type(e).__name__, 10 * (tentativo + 1)))
                interruzione.attendi(10 * (tentativo + 1))
        if os.path.getsize(parziale) != DIMENSIONE_ZIP or _sha256(parziale) != SHA256_ZIP:
            os.remove(parziale)
            raise ErroreLocale("Ollama scaricato non corrisponde all'impronta pubblicata: file scartato. Aggiorna "
                               "PatchSmith o riprova piu' tardi.")
        os.replace(parziale, zip_)
    _estrai(zip_, os.path.join(cartella(), VERSIONE))
    os.remove(zip_)
    log("  Ollama %s installato in %s" % (VERSIONE, os.path.join(cartella(), VERSIONE)))


def _scarica_zip(parziale, log):
    """Scarica lo zip in 'parziale', riprendendo da dove era (richiesta Range) se c'e' gia' un pezzo."""
    gia = os.path.getsize(parziale) if os.path.exists(parziale) else 0
    if gia >= DIMENSIONE_ZIP:
        return
    intestazioni = {"User-Agent": "PatchSmith (installazione traduttore locale)"}
    if gia:
        intestazioni["Range"] = "bytes=%d-" % gia
    with urllib.request.urlopen(urllib.request.Request(URL_ZIP, headers=intestazioni), timeout=60) as r:
        if gia and r.status != 206:  # il server non riprende: si ricomincia da zero
            gia = 0
        if gia:
            log("    riprendo da %.0f%%" % (100 * gia / DIMENSIONE_ZIP))
        letti, prossimo = gia, (gia // (DIMENSIONE_ZIP // 10) + 1) * (DIMENSIONE_ZIP // 10)
        with open(parziale, "ab" if gia else "wb") as f:
            for blocco in iter(lambda: r.read(1 << 20), b""):
                interruzione.controlla()  # Ferma: il pezzo scaricato resta per la prossima volta
                f.write(blocco)
                letti += len(blocco)
                if letti > DIMENSIONE_ZIP + 1024:
                    f.close()
                    os.remove(parziale)
                    raise ErroreLocale("Il file scaricato e' piu' grande del previsto: interrotto.")
                if letti >= prossimo:
                    log("    %.0f%%" % (100 * letti / DIMENSIONE_ZIP))
                    prossimo += DIMENSIONE_ZIP // 10


def _estrai(zip_, destinazione):
    """Estrae lo zip in una cartella temporanea controllando ogni percorso, poi la rinomina: mai file fuori posto."""
    temporanea = destinazione + ".tmp"
    shutil.rmtree(temporanea, ignore_errors=True)
    os.makedirs(temporanea)
    radice = os.path.realpath(temporanea)
    with zipfile.ZipFile(zip_) as z:
        for voce in z.infolist():
            nome = voce.filename.replace("\\", "/")
            percorso = os.path.realpath(os.path.join(temporanea, nome))
            if nome.startswith("/") or ":" in nome or ".." in nome.split("/") or \
                    not (percorso == radice or percorso.startswith(radice + os.sep)):
                shutil.rmtree(temporanea, ignore_errors=True)
                raise ErroreLocale("Lo zip di Ollama contiene un percorso non sicuro (%s): installazione annullata."
                                   % nome)
        z.extractall(temporanea)
    if not os.path.isfile(os.path.join(temporanea, "ollama.exe")) or \
            not os.path.isdir(os.path.join(temporanea, "lib", "ollama")):
        shutil.rmtree(temporanea, ignore_errors=True)
        raise ErroreLocale("Lo zip di Ollama non contiene il programma atteso: installazione annullata.")
    try:
        if os.path.exists(destinazione):  # installazione rovinata (es. ollama.exe tolto dall'antivirus): si rifa'
            scarto = destinazione + ".vecchia"
            shutil.rmtree(scarto, ignore_errors=True)
            os.replace(destinazione, scarto)
            shutil.rmtree(scarto, ignore_errors=True)
        os.replace(temporanea, destinazione)
    except OSError as e:
        shutil.rmtree(temporanea, ignore_errors=True)
        raise ErroreLocale("Non riesco a sostituire la cartella %s (%s): chiudi i programmi che la usano, oppure "
                           "cancellala a mano, e riprova." % (destinazione, e)) from None
