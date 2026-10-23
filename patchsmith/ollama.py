"""Local translator: Ollama (https://ollama.com, MIT license) with the TranslateGemma models (Gemma Terms of Use).

Everything is in PatchSmith's folder (components/ollama: program and models), nothing is installed in the system:
- the server starts only during a translation, on a local port of its own (127.0.0.1, 11435-11444), and is
  closed at the end; a Windows "Job Object" closes it anyway if PatchSmith stops abruptly;
- the user's OLLAMA_* variables are removed from the server's environment and PatchSmith's are set
  (models on D:, no cloud features);
- calls to the local server never go through a system proxy.
On C: Ollama writes only a small key in %USERPROFILE%\\.ollama.
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

from . import interruption

VERSION = "0.40.4"
ZIP_URL = "https://github.com/ollama/ollama/releases/download/v0.40.4/ollama-windows-amd64.zip"
ZIP_SIZE = 1469916992
SHA256_ZIP = "7485a1172f8527db4342a63521b9342fc466185ff0855da378cc864ecf6354cf"
MODELS = {
    "4b": {"nome": "translategemma:4b-it-q4_K_M", "impronta": "c49d986b0764", "gb": 3.3,
           "etichetta": "Fast (TranslateGemma 4B)", "secondi_per_testo": 0.25, "secondi_avvio": 30,
           "parallele": 4},  # test on RTX 3060 Ti: 4 lines together 0.10 s per line against 0.14
    "12b": {"nome": "translategemma:12b-it-q4_K_M", "impronta": "c2f9a9ca1ec7", "gb": 8.1,
            "etichetta": "Quality (TranslateGemma 12B)", "secondi_per_testo": 2.5, "secondi_avvio": 80},
}
MODEL_LICENSE = "https://ai.google.dev/gemma/terms"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LocalError(Exception):
    """The local translator is not installed, does not start or does not have enough memory."""


def folder():
    from . import project
    return project.components_folder("ollama")


def program():
    return os.path.join(folder(), VERSION, "ollama.exe")


def models_folder():
    return os.path.join(folder(), "modelli")


def installed():
    return os.path.isfile(program())


def model_present(key):
    """True if the model is already downloaded (its manifest is checked, without starting the server)."""
    name, tag = MODELS[key]["nome"].split(":")
    return os.path.isfile(os.path.join(models_folder(), "manifests", "registry.ollama.ai", "library", name, tag))


THRESHOLD_12B = 7.5  # GB of video card memory from which the 12B is recommended (8 GB cards report ~7.9)
_VIDEO_MEMORY = []  # the video memory already looked up (once per PatchSmith start)


def _nvidia_smi_program():
    """nvidia-smi.exe of the NVIDIA driver (System32, the NVSMI folder of old drivers, then the PATH), or None. Never
    from the current folder: a program with that name put there (for example in the folder of a downloaded game)
    must not start. Outside Windows: the one in the PATH."""
    if not sys.platform.startswith("win"):
        return shutil.which("nvidia-smi")
    candidates = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe"),
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NVIDIA Corporation", "NVSMI",
                              "nvidia-smi.exe")]
    candidates += [os.path.join(d.strip().strip('"'), "nvidia-smi.exe")
                  for d in os.environ.get("PATH", "").split(os.pathsep) if os.path.isabs(d.strip().strip('"'))]
    current_name = os.path.normcase(os.path.abspath(os.getcwd()))
    return next((p for p in candidates if os.path.isfile(p)
                 and os.path.normcase(os.path.dirname(os.path.abspath(p))) != current_name), None)


def _nvidia_smi():
    """GB of the largest NVIDIA card, from nvidia-smi (it comes with the NVIDIA driver), or None."""
    program = _nvidia_smi_program()
    if not program:
        return None
    try:
        r = subprocess.run([program, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, timeout=10, creationflags=0x08000000 if sys.platform.startswith("win")
                           else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    values = [int(x) for x in r.stdout.decode("ascii", "replace").split() if x.isdigit()]  # MiB
    return max(values) / 1024 if r.returncode == 0 and values else None


def _video_registry():
    """GB of the largest video card according to its driver (Windows registry, read-only), or None."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
        class_ = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}")
    except OSError:
        return None
    values, i = [], 0
    with class_:
        while True:
            try:
                name = winreg.EnumKey(class_, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(class_, name) as sheet:
                    value_, _ = winreg.QueryValueEx(sheet, "HardwareInformation.qwMemorySize")
            except OSError:
                continue
            if isinstance(value_, bytes):
                value_ = int.from_bytes(value_[:8], "little")
            if isinstance(value_, int) and value_ > 0:
                values.append(value_ / 1024 ** 3)
    return max(values) if values else None


def _wmi_video():
    """GB of the largest video card from WMI (PowerShell, read-only), or None. WMI goes up to 4 GB at most:
    a 4 GB value means "at least 4"."""
    if not sys.platform.startswith("win"):
        return None
    # the full path: with the name alone Windows would look in the current folder first
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
    values = [int(x) for x in r.stdout.decode("ascii", "replace").split() if x.isdigit()]
    if not values or max(values) >= 0xF0000000:  # at the 4 GB limit: the real memory is not known
        return None
    return max(values) / 1024 ** 3


def video_memory():
    """GB of memory of the video card (the largest), read without installing or downloading anything: nvidia-smi if
    available (NVIDIA driver), then the Windows registry (the value written by the driver), then WMI with PowerShell.
    None if not known. It is looked up only once."""
    if not _VIDEO_MEMORY:
        value_ = None
        for search in (_nvidia_smi, _video_registry, _wmi_video):
            try:
                value_ = search()
            except Exception:  # noqa: BLE001 - only a recommendation: an error here must not stop anything
                value_ = None
            if value_:
                break
        _VIDEO_MEMORY.append(value_)
    return _VIDEO_MEMORY[0]


def recommended_model(gb=None):
    """(model key, reason for the player): the 12B (makes fewer mistakes, but is slower) if the video card has
    at least THRESHOLD_12B GB of memory, otherwise the 4B (the 12B outside the video card becomes very slow)."""
    gb = video_memory() if gb is None else gb
    if gb is None:
        return "4b", ("I can't tell how much memory your graphics card has: I recommend the Fast model (4B), which "
                      "works on almost all PCs.")
    if gb >= THRESHOLD_12B:
        return "12b", ("Your graphics card has %s GB of memory: I recommend the Quality model (12B), which makes fewer "
                       "mistakes (it's slower than the Fast option)." % _gb(gb))
    return "4b", ("Your graphics card has %s GB of memory: I recommend the Fast model (4B). The Quality model (12B) "
                  "needs at least 8 GB, and with less it would become very slow." % _gb(gb))


CARD_THRESHOLD = 3.5  # GB: below this (integrated graphics or old card) the model runs almost entirely on the CPU
MIN_RAM = 8  # GB of PC memory: below this, even the 4B (about 4 GB) leaves little to the rest of Windows


def pc_memory():
    """GB of memory (RAM) of the PC, or None if not known."""
    if not sys.platform.startswith("win"):
        return None
    import ctypes

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    state = _MemoryStatus()
    state.dwLength = ctypes.sizeof(_MemoryStatus)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return state.ullTotalPhys / 1024 ** 3
    except (OSError, AttributeError):
        pass
    return None


def performance_warning(gb_video=None, gb_ram=None):
    """Text to show BEFORE downloading if this PC will make the local translator slow ('' if it is fine or not
    known): without a suitable video card (integrated graphics, old cards or cards with little memory) the model runs
    on the CPU, many times slower; with little RAM the rest of the PC slows down too."""
    gb_video = video_memory() if gb_video is None else gb_video
    gb_ram = pc_memory() if gb_ram is None else gb_ram
    warnings = []
    if gb_video is not None and gb_video < CARD_THRESHOLD:
        warnings.append("The graphics card in this PC has %s GB of memory (or it's the integrated graphics in the "
                        "processor): the local translator will primarily use the processor, and will be much slower, "
                        "possibly 10-20 times slower. A game with 2,000 sentences could take an hour or more instead "
                        "of just a few minutes." % _gb(gb_video))
    if gb_ram is not None and gb_ram < MIN_RAM:
        warnings.append("Your PC has %s GB of memory (RAM): while translating, the model uses about 4, and the rest of "
                        "the PC may slow down." % _gb(gb_ram))
    if not warnings:
        return ""
    return " ".join(warnings) + (" It works anyway; alternatively you can use DeepL with your free key (500,000 "
                                 "characters per month, nothing to install).")


def _gb(value_):
    text = "%.1f" % value_
    return text[:-2] if text.endswith(".0") else text


def default_model():
    """The best one already downloaded. Tests on Forager (RTX 3060 Ti 8 GB, October 2026): the 12B gets fewer names
    and less grammar wrong but is about 5 times slower (2.5 s per text against 0.5); it is downloaded only if the
    user chooses it, so if it is there it is wanted."""
    return next((k for k in ("12b", "4b") if model_present(k)), "4b")


def state():
    """'pronto', 'manca il modello' or 'non installato' (for the interface)."""
    if not installed():
        return "not installed"
    present = [k for k in MODELS if model_present(k)]
    return "pronto" if present else "the model is missing"


# --- Job Object: the server dies with PatchSmith ---------------------------------------------------

class _BASE(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RB", "WB", "OB")]


class _EXTENDED(ctypes.Structure):
    _fields_ = [("Base", _BASE), ("Io", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _bind_to_job(pid):
    """Puts the process in a Job Object that is closed (with all its children) when PatchSmith exits."""
    if not sys.platform.startswith("win"):
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.OpenProcess.restype = ctypes.c_void_p
        job = k32.CreateJobObjectW(None, None)
        info = _EXTENDED()
        info.Base.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        process = k32.OpenProcess(0x0101, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if not process:
            return None
        ok = k32.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(process))
        k32.CloseHandle(ctypes.c_void_p(process))
        return job if ok else None
    except (OSError, AttributeError):
        return None


# --- server ---------------------------------------------------------------------------------------

def _port_busy(port):
    """True if something already answers on this port (also listening on 0.0.0.0 or [::])."""
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as c:
                c.settimeout(0.2)
                if c.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def _free_port():
    for port in range(11435, 11445):
        if _port_busy(port):
            continue
        s = socket.socket()
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise LocalError("No free port available for the local translator (11435-11444).")


_LOG_LIMIT = 5 * 1024 * 1024


class Server:
    """PatchSmith's Ollama server: started with avvia(), closed with chiudi() (or with 'with')."""

    def __init__(self, log=print, executable=None, startup_wait=180.0, command=None, parallel=1):
        self.log = log
        self.executable = executable or program()
        self.command = command or [self.executable]  # the tests use a simulated Ollama
        self.startup_wait = startup_wait
        self.parallel = max(1, int(parallel))  # requests processed together by the video card
        self.process = None
        self.port = None
        self._job = None
        self._log_file = None
        self._log_start = 0  # position in the log at startup: errors show only this run's lines

    def __enter__(self):
        try:
            self.launch()
        except BaseException:  # Stop or error during startup: the server just launched does not stay on
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    def launch(self):
        if self.active:
            return
        if not os.path.isfile(self.command[0]):
            raise LocalError("The local translator is not installed: choose 'Local translator' and press 'Install'.")
        os.makedirs(models_folder(), exist_ok=True)
        temp_path = os.path.join(folder(), "tmp")
        os.makedirs(temp_path, exist_ok=True)
        self.port = _free_port()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("OLLAMA_") and k.upper() != "HTTP_PROXY"}
        env.update(OLLAMA_HOST="127.0.0.1:%d" % self.port, OLLAMA_MODELS=models_folder(), OLLAMA_NO_CLOUD="1",
                   OLLAMA_KEEP_ALIVE="30m", OLLAMA_NUM_PARALLEL=str(self.parallel), OLLAMA_MAX_LOADED_MODELS="1",
                   OLLAMA_CONTEXT_LENGTH="2048", OLLAMA_NOPRUNE="1", TEMP=temp_path, TMP=temp_path)
        if not any(k.upper() == "HTTPS_PROXY" for k in env):
            # proxy set only in Windows (registry): Ollama, written in Go, reads only the variables
            proxy = urllib.request.getproxies().get("https")
            if proxy:
                env["HTTPS_PROXY"] = proxy
        env["NO_PROXY"] = ",".join(x for x in (env.pop("NO_PROXY", ""), "127.0.0.1,localhost") if x)
        log_file = os.path.join(folder(), "server.log")
        try:
            if os.path.getsize(log_file) > _LOG_LIMIT:
                os.replace(log_file, log_file + ".1")
        except OSError:
            pass
        self._log_file = open(log_file, "ab")
        self._log_start = self._log_file.tell()
        self.process = subprocess.Popen(
            self.command + ["serve"], env=env, cwd=os.path.dirname(self.command[0]), stdin=subprocess.DEVNULL,
            stdout=self._log_file, stderr=subprocess.STDOUT,
            creationflags=(0x08000000 | 0x00000200) if sys.platform.startswith("win") else 0)
        try:
            self._job = _bind_to_job(self.process.pid)
            limit = time.monotonic() + self.startup_wait
            while time.monotonic() < limit:
                interruption.check()
                if self.process.poll() is not None:
                    raise LocalError("The local translator didn't start:\n%s" % _log_tail(self._log_start))
                try:
                    version = self.call("/api/version", timeout=2).get("version")
                except (OSError, ValueError, AttributeError):
                    time.sleep(0.25)
                    continue
                # is it our server answering (not another program on the same port)?
                if version == VERSION and self.process.poll() is None:
                    return
                time.sleep(0.25)
            raise LocalError("The local translator is not responding after %d seconds:\n%s"
                               % (self.startup_wait, _log_tail(self._log_start)))
        except BaseException:  # error, Stop or Ctrl+C during startup: no server left on
            self.close()
            raise

    def call(self, path, data=None, timeout=180):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=json.dumps(data).encode("utf-8") if data is not None else None,
                                     headers={"Content-Type": "application/json"})
        with _NO_PROXY.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def stream(self, path, data, timeout=3600):
        """NDJSON lines of a streaming response (e.g. /api/pull)."""
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=json.dumps(data).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with _NO_PROXY.open(req, timeout=timeout) as r:
            for line in r:
                interruption.check()
                if line.strip():
                    yield json.loads(line.decode("utf-8"))

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                if sys.platform.startswith("win"):
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True,
                                   creationflags=0x08000000)
                else:
                    self.process.kill()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            self.process = None
        if self._job:
            try:
                ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(self._job))
            except (OSError, AttributeError):
                pass
            self._job = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None


def _log_tail(from_pos=0, lines=15):
    """The last lines of the log written from 'dalla' onwards (at most the last 64 KB)."""
    try:
        with open(os.path.join(folder(), "server.log"), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(from_pos, f.tell() - 65536))
            text = f.read().decode("utf-8", "replace").splitlines()[-lines:]
        return "\n".join(text) or "(The server hasn't written anything)"
    except OSError:
        return "(server log not available)"


# --- installation (only with the user's consent) --------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download_description(selected):
    """Text to show BEFORE downloading (file, source, size, license)."""
    lines = []
    if not installed():
        lines.append("Ollama %s: ollama-windows-amd64.zip, %.2f GB, from github.com/ollama/ollama (official release, "
                     "MIT license, SHA-256 hash verified)" % (VERSION, ZIP_SIZE / 1e9))
    for k in selected:
        if not model_present(k):
            lines.append("%s: %s, %.1f GB, from the official Ollama registry (ollama.com). Gemma Terms of Use License "
                         "(%s): by downloading it, you agree to the terms" % (MODELS[k]["etichetta"], MODELS[k]["nome"],
                                                                MODELS[k]["gb"], MODEL_LICENSE))
    return lines


def install(selected, log=print):
    """Downloads (if missing) and installs Ollama and the chosen models in components/ollama. To be called only after
    the user's consent. It can be stopped: downloads resume from where they were."""
    needed = (0 if installed() else ZIP_SIZE * 3.5) + sum(MODELS[k]["gb"] * 1e9 for k in selected
                                                                  if not model_present(k))
    os.makedirs(folder(), exist_ok=True)
    if shutil.disk_usage(folder()).free < needed + 1e9:
        raise LocalError("Insufficient space: you need approximately %.0f GB of free space on %s."
                         % ((needed + 1e9) / 1e9, os.path.splitdrive(folder())[0]))
    try:
        if not installed():
            _install_program(log)
        _download_models(selected, log)
    except (OSError, ValueError) as e:  # network, disk, unexpected response: an understandable message, not a traceback
        raise LocalError("Installation failed (%s: %s). Check your connection and try again: downloads will resume "
                         "from where they left off." % (type(e).__name__, e)) from None
    return True


def _download_models(selected, log):
    with Server(log) as server:
        for k in selected:
            if model_present(k):
                log("  %s: Already downloaded" % MODELS[k]["etichetta"])
                continue
            log("  Downloading %s (%.1f GB)..." % (MODELS[k]["etichetta"], MODELS[k]["gb"]))
            parts, last = {}, -10.0
            for pull_state in server.stream("/api/pull", {"model": MODELS[k]["nome"], "stream": True}):
                if "error" in pull_state:
                    raise LocalError("Model download failed: %s" % pull_state["error"])
                if pull_state.get("digest") and pull_state.get("total"):
                    parts[pull_state["digest"]] = (pull_state.get("completed", 0), pull_state["total"])
                    done = 100 * sum(c for c, _ in parts.values()) / max(1, sum(t for _, t in parts.values()))
                    if done >= last + 10:
                        last = done
                        log("    %.0f%%" % done)
            present = {m.get("name"): m.get("digest", "") for m in server.call("/api/tags").get("models", [])}
            if not present.get(MODELS[k]["nome"], "").startswith(MODELS[k]["impronta"]):
                log("  ! %s: hash different from the expected one (has the model been updated?)" % MODELS[k]["nome"])
            log("  %s ready." % MODELS[k]["etichetta"])


def _install_program(log):
    zip_ = os.path.join(os.path.dirname(folder()), "ollama-windows-amd64-%s.zip" % VERSION)
    if not (os.path.exists(zip_) and os.path.getsize(zip_) == ZIP_SIZE and _sha256(zip_) == SHA256_ZIP):
        partial = zip_ + ".scaricamento"
        log("  Downloading Ollama %s (%.2f GB) from github.com..." % (VERSION, ZIP_SIZE / 1e9))
        for attempt in range(3):
            try:
                _download_zip(partial, log)
                break
            except LocalError:
                raise
            except OSError as e:  # network: the part already downloaded stays, it resumes from there
                if attempt == 2:
                    raise
                log("    connection lost (%s): retrying in %d seconds..."
                    % (type(e).__name__, 10 * (attempt + 1)))
                interruption.wait_for(10 * (attempt + 1))
        if os.path.getsize(partial) != ZIP_SIZE or _sha256(partial) != SHA256_ZIP:
            os.remove(partial)
            raise LocalError("The downloaded Ollama doesn't match the published hash: file discarded. Update "
                             "PatchSmith or try again later.")
        os.replace(partial, zip_)
    _extract(zip_, os.path.join(folder(), VERSION))
    os.remove(zip_)
    log("  Ollama %s installed in %s" % (VERSION, os.path.join(folder(), VERSION)))


def _download_zip(partial, log):
    """Downloads the zip into 'parziale', resuming from where it was (Range request) if a part is already there."""
    already = os.path.getsize(partial) if os.path.exists(partial) else 0
    if already >= ZIP_SIZE:
        return
    http_headers = {"User-Agent": "PatchSmith (local translator installation)"}
    if already:
        http_headers["Range"] = "bytes=%d-" % already
    with urllib.request.urlopen(urllib.request.Request(ZIP_URL, headers=http_headers), timeout=60) as r:
        if already and r.status != 206:  # the server does not resume: start again from zero
            already = 0
        if already:
            log("    resuming from %.0f%%" % (100 * already / ZIP_SIZE))
        read_items, next_mark = already, (already // (ZIP_SIZE // 10) + 1) * (ZIP_SIZE // 10)
        with open(partial, "ab" if already else "wb") as f:
            for block in iter(lambda: r.read(1 << 20), b""):
                interruption.check()  # Stop: the downloaded part stays for next time
                f.write(block)
                read_items += len(block)
                if read_items > ZIP_SIZE + 1024:
                    f.close()
                    os.remove(partial)
                    raise LocalError("The downloaded file is larger than expected: download aborted.")
                if read_items >= next_mark:
                    log("    %.0f%%" % (100 * read_items / ZIP_SIZE))
                    next_mark += ZIP_SIZE // 10


def _extract(zip_, dest_lang):
    """Extracts the zip into a temporary folder checking every path, then renames it: never files out of place."""
    temp_path = dest_lang + ".tmp"
    shutil.rmtree(temp_path, ignore_errors=True)
    os.makedirs(temp_path)
    root = os.path.realpath(temp_path)
    with zipfile.ZipFile(zip_) as z:
        for entry in z.infolist():
            name = entry.filename.replace("\\", "/")
            path = os.path.realpath(os.path.join(temp_path, name))
            if name.startswith("/") or ":" in name or ".." in name.split("/") or \
                    not (path == root or path.startswith(root + os.sep)):
                shutil.rmtree(temp_path, ignore_errors=True)
                raise LocalError("The Ollama zip contains an insecure path (%s): installation cancelled."
                                   % name)
        z.extractall(temp_path)
    if not os.path.isfile(os.path.join(temp_path, "ollama.exe")) or \
            not os.path.isdir(os.path.join(temp_path, "lib", "ollama")):
        shutil.rmtree(temp_path, ignore_errors=True)
        raise LocalError("The Ollama zip file does not contain the expected program: installation cancelled.")
    try:
        if os.path.exists(dest_lang):  # damaged installation (e.g. ollama.exe removed by the antivirus): redo it
            old_copy = dest_lang + ".vecchia"
            shutil.rmtree(old_copy, ignore_errors=True)
            os.replace(dest_lang, old_copy)
            shutil.rmtree(old_copy, ignore_errors=True)
        os.replace(temp_path, dest_lang)
    except OSError as e:
        shutil.rmtree(temp_path, ignore_errors=True)
        raise LocalError("I can't replace the folder %s (%s): close the programs that are using it, or delete it by "
                         "hand, and try again." % (dest_lang, e)) from None
