"""PatchSmith package to publish: a folder (and its zip) that works even on a PC without Python.

Inside: the program (PatchSmith.pyw, the command-line script, patchsmith/, the plugin sources and the data it
needs), PatchSmith.exe (the launcher with the icon, see icon.executable) and in runtime/ a "portable" Python: only
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

from . import __version__, icon, project

PROGRAM = icon.PROGRAM
PROGRAM_FILES = ("PatchSmith.pyw", "smith.py", "LEGGIMI.md", "README.md", "LICENSE")
# program folders and the files to take (the compiled plugins and the downloaded components do not go in)
PROGRAM_FOLDERS = {"patchsmith": ("*",), "data": ("*",), "docs": ("*",),
                      os.path.join("components", "plugin"): ("*.cs",)}
SKIP = ("__pycache__", "*.pyc", "*.tmp", "PatchSmith.nuovo.exe")
# from Python: what PatchSmith does not use (tests, IDLE editor, pip, demos...)
SKIP_LIB = ("site-packages", "test", "tests", "idle_test", "idlelib", "turtledemo", "ensurepip", "lib2to3",
             "pydoc_data", "__pycache__", "*.pyc")
SKIP_DLL = ("_test*", "_ctypes_test*", "xxlimited*")
SKIP_TCL = ("tix*", "demos", "nmake", "*.lib", "*.sh")  # (the files for compiling are not needed)
# modules the portable Python must have (the test at the end of the package)
MODULES = ("tkinter", "ssl", "ctypes", "zlib", "sqlite3", "json", "http.server", "urllib.request", "winreg")


class PackageError(Exception):
    pass


def _copy_tree(source, dest_lang, skip=(), take=("*",)):
    """Copies the folder without the names that match 'salta'; of the files, only those that match 'prendi'."""
    for root, folders, file in os.walk(source):
        folders[:] = [c for c in folders if not any(fnmatch.fnmatch(c, s) for s in skip)]
        where = os.path.join(dest_lang, os.path.relpath(root, source))
        for name in file:
            if any(fnmatch.fnmatch(name, s) for s in skip) or not any(fnmatch.fnmatch(name, p) for p in take):
                continue
            os.makedirs(where, exist_ok=True)
            shutil.copy2(os.path.join(root, name), os.path.join(where, name))


def _portable_python(python, runtime, log):
    """runtime/: executables, DLLs, standard library and Tcl/Tk of the Python installed in 'python', plus the ._pth
    file."""
    version = "%d%d" % sys.version_info[:2] if os.path.normcase(python) == os.path.normcase(sys.base_prefix) else None
    if version is None:  # another Python: the version from the name of its DLL (python312.dll...)
        dll = [n for n in os.listdir(python)
               if fnmatch.fnmatch(n.lower(), "python3?*.dll") and n.lower() != "python3.dll"]
        if not dll:
            raise PackageError("in directory %s, the Python DLL (python3XX.dll) is not found" % python)
        version = dll[0][len("python"):-len(".dll")]
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python%s.dll" % version):
        if not os.path.isfile(os.path.join(python, name)):
            raise PackageError("%s is missing %s: you need a Python installed from python.org" % (python, name))
    if not (os.path.isfile(os.path.join(python, "DLLs", "_tkinter.pyd"))
            and os.path.isdir(os.path.join(python, "tcl"))):
        raise PackageError("the Python version in %s doesn't include Tkinter (the graphical interface): install it "
                           'using the "tcl/tk and IDLE" option'
                              % python)
    os.makedirs(runtime)
    for name in os.listdir(python):
        if name.lower().endswith((".exe", ".dll")) or name == "LICENSE.txt":
            shutil.copy2(os.path.join(python, name), os.path.join(runtime, name))
    _copy_tree(os.path.join(python, "DLLs"), os.path.join(runtime, "DLLs"), SKIP_DLL, ("*.pyd", "*.dll"))
    _copy_tree(os.path.join(python, "Lib"), os.path.join(runtime, "Lib"), SKIP_LIB)
    _copy_tree(os.path.join(python, "tcl"), os.path.join(runtime, "tcl"), SKIP_TCL)
    # the ._pth: only these paths, no PYTHONPATH/PYTHONHOME nor registry (another installed Python does not count)
    with open(os.path.join(runtime, "python%s._pth" % version), "w", encoding="utf-8") as f:
        f.write("Lib\nDLLs\n.\n")
    log("  Portable Python %s.%s from %s" % (version[0], version[1:], python))


def _try_runtime(folder):
    """The portable Python starts on its own (environment without Python: Windows PATH and no PYTHON* variable) and
    has the modules it needs, Tkinter with its Tcl included. Returns the executable it used."""
    python = os.path.join(folder, "runtime", "python.exe")
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "TCL_", "TK_"))}
    environment["PATH"] = os.path.join(windows, "System32") + ";" + windows
    code = ("import importlib, sys; [importlib.import_module(m) for m in %r]; import tkinter; "
              "print(sys.executable); print(tkinter.Tcl().eval('info patchlevel'))" % (MODULES,))
    r = subprocess.run([python, "-I", "-c", code], capture_output=True, timeout=60, env=environment,
                       cwd=os.path.join(windows, "System32"), creationflags=0x08000000)
    lines = r.stdout.decode("utf-8", "replace").split()
    if r.returncode != 0 or len(lines) < 2:
        raise PackageError("the portable Python isn't starting: %s"
                           % r.stderr.decode("utf-8", "replace").strip()[-300:])
    return lines[0], lines[1]


def prepare(output=None, python=None, log=print):
    """Prepares <uscita>/PatchSmith and <uscita>/PatchSmith-<versione>-windows.zip. uscita: by default the folder
    PatchSmith-rilascio next to the program's; python: the folder of an installed Python (by default the one
    PatchSmith runs with). Returns (cartella, zip)."""
    if not sys.platform.startswith("win"):
        raise PackageError("the package with the portable Python version is for Windows")
    python = os.path.abspath(python or sys.base_prefix)
    output = os.path.abspath(output or os.path.join(os.path.dirname(PROGRAM), "PatchSmith-rilascio"))
    if os.path.normcase(output).startswith(os.path.normcase(PROGRAM) + os.sep):
        raise PackageError("the package cannot be placed inside the program folder")
    folder = os.path.join(output, "PatchSmith")
    if os.path.exists(folder):
        data = [n for n in project.USER_DATA + project.USER_DATA_BEFORE if os.path.exists(os.path.join(folder, n))]
        if data:  # someone used the package from here: their data is not deleted
            raise PackageError("%s contains user data (%s): move it or choose another folder"
                                  % (folder, ", ".join(data)))
        if not os.path.isfile(os.path.join(folder, "PatchSmith.pyw")):
            raise PackageError("%s already exists, and it's not a PatchSmith package: please choose a different folder"
                                  % folder)
        shutil.rmtree(folder)  # the previous package
    log("PatchSmith %s package in %s" % (__version__, folder))
    os.makedirs(folder)
    for name in PROGRAM_FILES:
        shutil.copy2(os.path.join(PROGRAM, name), os.path.join(folder, name))
    for rel, take in PROGRAM_FOLDERS.items():
        _copy_tree(os.path.join(PROGRAM, rel), os.path.join(folder, rel), SKIP, take)
    _portable_python(python, os.path.join(folder, "runtime"), log)
    if not icon.executable(folder):
        raise PackageError("I wasn't able to compile PatchSmith.exe (the Windows C# compiler)")
    executable, tcl = _try_runtime(folder)
    log("  Test: The portable Python interpreter starts automatically (%s, Tcl/Tk %s)" % (executable, tcl))
    archive = os.path.join(output, "PatchSmith-%s-windows.zip" % __version__)
    temp_file = archive + ".tmp"
    with zipfile.ZipFile(temp_file, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, folders, file in os.walk(folder):
            folders[:] = [c for c in folders if c != "__pycache__"]  # (those left by the test)
            for name in sorted(file):
                path = os.path.join(root, name)
                z.write(path, os.path.join("PatchSmith", os.path.relpath(path, folder)))
    os.replace(temp_file, archive)
    weight = sum(os.path.getsize(os.path.join(r, n)) for r, _c, f in os.walk(folder) for n in f)
    log("  Folder: %.0f MB; zip: %.0f MB (%s)" % (weight / 1024 ** 2, os.path.getsize(archive) / 1024 ** 2, archive))
    return folder, archive
