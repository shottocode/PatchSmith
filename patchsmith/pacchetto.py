"""Pacchetto di PatchSmith da pubblicare: una cartella (e il suo zip) che funziona anche su un PC senza Python.

Dentro: il programma (PatchSmith.pyw, lo script della riga di comando, patchsmith/, i sorgenti dei plugin e i dati
che servono), PatchSmith.exe (l'avvio con l'icona, vedi icona.eseguibile) e in runtime/ un Python "portatile": le
sole parti di un Python installato che servono a PatchSmith (Tkinter compresa), con il file ._pth che lo isola dalle
variabili d'ambiente e dal registro di Windows. PatchSmith.exe usa quello se c'e', se no il Python installato; il
traduttore che parte con i giochi (Unity, web dal vivo) usa il Python con cui gira PatchSmith, quindi anche lui quello
portatile.
I dati di chi usa PatchSmith (profili, memoria, registri...) nel pacchetto non ci sono mai.

Nello zip c'e' una sola cartella PatchSmith: estratta sopra quella di prima la aggiorna e lascia i dati come sono.
"""
import fnmatch
import os
import shutil
import subprocess
import sys
import zipfile

from . import __version__, icona, progetto

PROGRAMMA = icona.PROGRAMMA
FILE_PROGRAMMA = ("PatchSmith.pyw", "smith.py", "LEGGIMI.md", "README.md", "LICENSE")
# cartelle del programma e i file da prendere (i plugin compilati e i componenti scaricati non ci vanno)
CARTELLE_PROGRAMMA = {"patchsmith": ("*",), "data": ("*",), "docs": ("*",),
                      os.path.join("components", "plugin"): ("*.cs",)}
SALTA = ("__pycache__", "*.pyc", "*.tmp", "PatchSmith.nuovo.exe")
# del Python: quello che PatchSmith non usa (prove, editor IDLE, pip, demo...)
SALTA_LIB = ("site-packages", "test", "tests", "idle_test", "idlelib", "turtledemo", "ensurepip", "lib2to3",
             "pydoc_data", "__pycache__", "*.pyc")
SALTA_DLL = ("_test*", "_ctypes_test*", "xxlimited*")
SALTA_TCL = ("tix*", "demos", "nmake", "*.lib", "*.sh")  # (i file per compilare non servono)
# moduli che il Python portatile deve avere (la prova alla fine del pacchetto)
MODULI = ("tkinter", "ssl", "ctypes", "zlib", "sqlite3", "json", "http.server", "urllib.request", "winreg")


class ErrorePacchetto(Exception):
    pass


def _copia_albero(sorgente, destinazione, salta=(), prendi=("*",)):
    """Copia la cartella senza i nomi che corrispondono a 'salta'; dei file solo quelli che corrispondono a 'prendi'."""
    for radice, cartelle, file in os.walk(sorgente):
        cartelle[:] = [c for c in cartelle if not any(fnmatch.fnmatch(c, s) for s in salta)]
        dove = os.path.join(destinazione, os.path.relpath(radice, sorgente))
        for nome in file:
            if any(fnmatch.fnmatch(nome, s) for s in salta) or not any(fnmatch.fnmatch(nome, p) for p in prendi):
                continue
            os.makedirs(dove, exist_ok=True)
            shutil.copy2(os.path.join(radice, nome), os.path.join(dove, nome))


def _python_portatile(python, runtime, log):
    """runtime/: eseguibili, DLL, libreria standard e Tcl/Tk del Python installato in 'python', piu' il file ._pth."""
    versione = "%d%d" % sys.version_info[:2] if os.path.normcase(python) == os.path.normcase(sys.base_prefix) else None
    if versione is None:  # un altro Python: la versione dal nome della sua DLL (python312.dll...)
        dll = [n for n in os.listdir(python)
               if fnmatch.fnmatch(n.lower(), "python3?*.dll") and n.lower() != "python3.dll"]
        if not dll:
            raise ErrorePacchetto("in %s non c'e' la DLL di Python (python3XX.dll)" % python)
        versione = dll[0][len("python"):-len(".dll")]
    for nome in ("python.exe", "pythonw.exe", "python3.dll", "python%s.dll" % versione):
        if not os.path.isfile(os.path.join(python, nome)):
            raise ErrorePacchetto("in %s manca %s: serve un Python installato da python.org" % (python, nome))
    if not (os.path.isfile(os.path.join(python, "DLLs", "_tkinter.pyd"))
            and os.path.isdir(os.path.join(python, "tcl"))):
        raise ErrorePacchetto("il Python in %s non ha Tkinter (la parte grafica): installalo con \"tcl/tk and IDLE\""
                              % python)
    os.makedirs(runtime)
    for nome in os.listdir(python):
        if nome.lower().endswith((".exe", ".dll")) or nome == "LICENSE.txt":
            shutil.copy2(os.path.join(python, nome), os.path.join(runtime, nome))
    _copia_albero(os.path.join(python, "DLLs"), os.path.join(runtime, "DLLs"), SALTA_DLL, ("*.pyd", "*.dll"))
    _copia_albero(os.path.join(python, "Lib"), os.path.join(runtime, "Lib"), SALTA_LIB)
    _copia_albero(os.path.join(python, "tcl"), os.path.join(runtime, "tcl"), SALTA_TCL)
    # il ._pth: solo questi percorsi, niente PYTHONPATH/PYTHONHOME ne' registro (un altro Python installato non conta)
    with open(os.path.join(runtime, "python%s._pth" % versione), "w", encoding="utf-8") as f:
        f.write("Lib\nDLLs\n.\n")
    log("  Python portatile %s.%s da %s" % (versione[0], versione[1:], python))


def _prova(cartella):
    """Il Python portatile parte da solo (ambiente senza Python: PATH di Windows e nessuna variabile PYTHON*) e ha
    i moduli che servono, Tkinter con il suo Tcl compreso. Restituisce l'eseguibile che ha usato."""
    python = os.path.join(cartella, "runtime", "python.exe")
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    ambiente = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "TCL_", "TK_"))}
    ambiente["PATH"] = os.path.join(windows, "System32") + ";" + windows
    codice = ("import importlib, sys; [importlib.import_module(m) for m in %r]; import tkinter; "
              "print(sys.executable); print(tkinter.Tcl().eval('info patchlevel'))" % (MODULI,))
    r = subprocess.run([python, "-I", "-c", codice], capture_output=True, timeout=60, env=ambiente,
                       cwd=os.path.join(windows, "System32"), creationflags=0x08000000)
    righe = r.stdout.decode("utf-8", "replace").split()
    if r.returncode != 0 or len(righe) < 2:
        raise ErrorePacchetto("il Python portatile non parte: %s" % r.stderr.decode("utf-8", "replace").strip()[-300:])
    return righe[0], righe[1]


def prepara(uscita=None, python=None, log=print):
    """Prepara <uscita>/PatchSmith e <uscita>/PatchSmith-<versione>-windows.zip. uscita: per difetto la cartella
    PatchSmith-rilascio accanto a quella del programma; python: la cartella di un Python installato (per difetto
    quello con cui gira PatchSmith). Restituisce (cartella, zip)."""
    if not sys.platform.startswith("win"):
        raise ErrorePacchetto("il pacchetto con il Python portatile e' per Windows")
    python = os.path.abspath(python or sys.base_prefix)
    uscita = os.path.abspath(uscita or os.path.join(os.path.dirname(PROGRAMMA), "PatchSmith-rilascio"))
    if os.path.normcase(uscita).startswith(os.path.normcase(PROGRAMMA) + os.sep):
        raise ErrorePacchetto("il pacchetto non puo' stare dentro la cartella del programma")
    cartella = os.path.join(uscita, "PatchSmith")
    if os.path.exists(cartella):
        dati = [n for n in progetto.DATI_UTENTE + progetto.DATI_UTENTE_PRIMA if os.path.exists(os.path.join(cartella, n))]
        if dati:  # qualcuno ha usato il pacchetto da qui: i suoi dati non si cancellano
            raise ErrorePacchetto("in %s ci sono dati di chi lo usa (%s): spostali o scegli un'altra cartella"
                                  % (cartella, ", ".join(dati)))
        if not os.path.isfile(os.path.join(cartella, "PatchSmith.pyw")):
            raise ErrorePacchetto("%s c'e' gia' e non e' un pacchetto di PatchSmith: scegli un'altra cartella"
                                  % cartella)
        shutil.rmtree(cartella)  # il pacchetto di prima
    log("Pacchetto di PatchSmith %s in %s" % (__version__, cartella))
    os.makedirs(cartella)
    for nome in FILE_PROGRAMMA:
        shutil.copy2(os.path.join(PROGRAMMA, nome), os.path.join(cartella, nome))
    for rel, prendi in CARTELLE_PROGRAMMA.items():
        _copia_albero(os.path.join(PROGRAMMA, rel), os.path.join(cartella, rel), SALTA, prendi)
    _python_portatile(python, os.path.join(cartella, "runtime"), log)
    if not icona.eseguibile(cartella):
        raise ErrorePacchetto("non sono riuscito a compilare PatchSmith.exe (compilatore C# di Windows)")
    eseguibile, tcl = _prova(cartella)
    log("  Prova: il Python portatile parte da solo (%s, Tcl/Tk %s)" % (eseguibile, tcl))
    archivio = os.path.join(uscita, "PatchSmith-%s-windows.zip" % __version__)
    temporaneo = archivio + ".tmp"
    with zipfile.ZipFile(temporaneo, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for radice, cartelle, file in os.walk(cartella):
            cartelle[:] = [c for c in cartelle if c != "__pycache__"]  # (quelli lasciati dalla prova)
            for nome in sorted(file):
                percorso = os.path.join(radice, nome)
                z.write(percorso, os.path.join("PatchSmith", os.path.relpath(percorso, cartella)))
    os.replace(temporaneo, archivio)
    peso = sum(os.path.getsize(os.path.join(r, n)) for r, _c, f in os.walk(cartella) for n in f)
    log("  Cartella: %.0f MB; zip: %.0f MB (%s)" % (peso / 1024 ** 2, os.path.getsize(archivio) / 1024 ** 2, archivio))
    return cartella, archivio
