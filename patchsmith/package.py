"""PatchSmith package to publish: a folder (and its zip) that works even on a PC without Python.

Inside: the program (PatchSmith.pyw, the command-line script, patchsmith/, the plugin sources and the data it
needs), PatchSmith.exe (the launcher with the icon, see icona.eseguibile) and in runtime/ a "portable" Python: only
the parts of an installed Python that PatchSmith needs (Tkinter included), with the ._pth file that isolates it from
the environment variables and from the Windows registry. PatchSmith.exe uses it if it is there, otherwise the
installed Python; the translator that starts with the games (Unity, live web) uses the Python PatchSmith runs with,
so it too uses the portable one.
The data of whoever uses PatchSmith (game profiles, translation memory, logs...) is never in the package.

The zip contains a single PatchSmith folder: extracted over the previous one, it updates it and leaves the data as
it is.
"""
import fnmatch
import os
import shutil
import subprocess
import sys
import zipfile

from . import __version__, icon as icona, project as progetto

PROGRAMMA = icona.PROGRAMMA
FILE_PROGRAMMA = ("PatchSmith.pyw", "smith.py", "LEGGIMI.md", "README.md", "LICENSE")
# program folders and the files to take (the compiled plugins and the downloaded components do not go in)
CARTELLE_PROGRAMMA = {"patchsmith": ("*",), "data": ("*",), "docs": ("*",),
                      os.path.join("components", "plugin"): ("*.cs",)}
SALTA = ("__pycache__", "*.pyc", "*.tmp", "PatchSmith.nuovo.exe")
# from Python: what PatchSmith does not use (tests, IDLE editor, pip, demos...)
SALTA_LIB = ("site-packages", "test", "tests", "idle_test", "idlelib", "turtledemo", "ensurepip", "lib2to3",
             "pydoc_data", "__pycache__", "*.pyc")
SALTA_DLL = ("_test*", "_ctypes_test*", "xxlimited*")
SALTA_TCL = ("tix*", "demos", "nmake", "*.lib", "*.sh")  # (the files for compiling are not needed)
# modules the portable Python must have (the test at the end of the package)
MODULI = ("tkinter", "ssl", "ctypes", "zlib", "sqlite3", "json", "http.server", "urllib.request", "winreg")


class ErrorePacchetto(Exception):
    pass


def _copia_albero(sorgente, destinazione, salta=(), prendi=("*",)):
    """Copies the folder without the names that match 'salta'; of the files, only those that match 'prendi'."""
    for radice, cartelle, file in os.walk(sorgente):
        cartelle[:] = [c for c in cartelle if not any(fnmatch.fnmatch(c, s) for s in salta)]
        dove = os.path.join(destinazione, os.path.relpath(radice, sorgente))
        for nome in file:
            if any(fnmatch.fnmatch(nome, s) for s in salta) or not any(fnmatch.fnmatch(nome, p) for p in prendi):
                continue
            os.makedirs(dove, exist_ok=True)
            shutil.copy2(os.path.join(radice, nome), os.path.join(dove, nome))


def _python_portatile(python, runtime, log):
    """runtime/: executables, DLLs, standard library and Tcl/Tk of the Python installed in 'python', plus the ._pth
    file."""
    versione = "%d%d" % sys.version_info[:2] if os.path.normcase(python) == os.path.normcase(sys.base_prefix) else None
    if versione is None:  # another Python: the version from the name of its DLL (python312.dll...)
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
    # the ._pth: only these paths, no PYTHONPATH/PYTHONHOME nor registry (another installed Python does not count)
    with open(os.path.join(runtime, "python%s._pth" % versione), "w", encoding="utf-8") as f:
        f.write("Lib\nDLLs\n.\n")
    log("  Python portatile %s.%s da %s" % (versione[0], versione[1:], python))


def _prova(cartella):
    """The portable Python starts on its own (environment without Python: Windows PATH and no PYTHON* variable) and
    has the modules it needs, Tkinter with its Tcl included. Returns the executable it used."""
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
    """Prepares <uscita>/PatchSmith and <uscita>/PatchSmith-<versione>-windows.zip. uscita: by default the folder
    PatchSmith-rilascio next to the program's; python: the folder of an installed Python (by default the one
    PatchSmith runs with). Returns (cartella, zip)."""
    if not sys.platform.startswith("win"):
        raise ErrorePacchetto("il pacchetto con il Python portatile e' per Windows")
    python = os.path.abspath(python or sys.base_prefix)
    uscita = os.path.abspath(uscita or os.path.join(os.path.dirname(PROGRAMMA), "PatchSmith-rilascio"))
    if os.path.normcase(uscita).startswith(os.path.normcase(PROGRAMMA) + os.sep):
        raise ErrorePacchetto("il pacchetto non puo' stare dentro la cartella del programma")
    cartella = os.path.join(uscita, "PatchSmith")
    if os.path.exists(cartella):
        dati = [n for n in progetto.DATI_UTENTE + progetto.DATI_UTENTE_PRIMA if os.path.exists(os.path.join(cartella, n))]
        if dati:  # someone used the package from here: their data is not deleted
            raise ErrorePacchetto("in %s ci sono dati di chi lo usa (%s): spostali o scegli un'altra cartella"
                                  % (cartella, ", ".join(dati)))
        if not os.path.isfile(os.path.join(cartella, "PatchSmith.pyw")):
            raise ErrorePacchetto("%s c'e' gia' e non e' un pacchetto di PatchSmith: scegli un'altra cartella"
                                  % cartella)
        shutil.rmtree(cartella)  # the previous package
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
            cartelle[:] = [c for c in cartelle if c != "__pycache__"]  # (those left by the test)
            for nome in sorted(file):
                percorso = os.path.join(radice, nome)
                z.write(percorso, os.path.join("PatchSmith", os.path.relpath(percorso, cartella)))
    os.replace(temporaneo, archivio)
    peso = sum(os.path.getsize(os.path.join(r, n)) for r, _c, f in os.walk(cartella) for n in f)
    log("  Cartella: %.0f MB; zip: %.0f MB (%s)" % (peso / 1024 ** 2, os.path.getsize(archivio) / 1024 ** 2, archivio))
    return cartella, archivio
