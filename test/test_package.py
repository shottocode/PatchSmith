"""Package to publish (package.py): folder and zip with PatchSmith.exe and the portable Python.

Usage: python -I test/test_package.py <test_folder_that_does_not_exist>
- the package with the program, PatchSmith.exe and runtime/ (Python with Tkinter and the ._pth file), without the data
  of whoever uses PatchSmith, the tests and the downloaded components; the zip with only the PatchSmith folder, without
  __pycache__;
- the portable Python starts on its own (prepare already tests it) and runs smith.py;
- PatchSmith.exe chooses the portable Python even with an installed Python (here a test PatchSmith.pyw);
- a used package (with data inside) is not deleted; the package does not go inside the program's folder.
"""
import os
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import package, project  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    if not sys.platform.startswith("win"):
        print("Solo Windows: niente da provare.")
        return 0
    lines = []
    folder, archive = package.prepare(os.path.join(base, "rilascio"), log=lines.append)
    print("\n".join(lines))
    runtime = os.path.join(folder, "runtime")
    expected_list = ["PatchSmith.exe", "PatchSmith.pyw", "smith.py", "LEGGIMI.md", "README.md", "LICENSE",
              "patchsmith/gui.py", "patchsmith/launcher.cs", "docs/TECNICO.md", "docs/TECHNICAL.md",
              "patchsmith/icon/16.png", "patchsmith/ui_language/it.json", "data/rules.ini",
              "components/plugin/PatchsmithServizio.cs", "runtime/pythonw.exe", "runtime/DLLs/_tkinter.pyd",
              "runtime/Lib/tkinter/__init__.py", "runtime/LICENSE.txt"]
    absent = [a for a in expected_list if not os.path.isfile(os.path.join(folder, *a.split("/")))]
    pth = [n for n in os.listdir(runtime) if n.endswith("._pth")]
    # the Tcl library: the tcl8.6 folder with init.tcl, or from Tcl 9 the libtcl9.x.zip zip
    tcl = [n for n in os.listdir(os.path.join(runtime, "tcl")) if n.startswith("libtcl") and n.endswith(".zip")
           or os.path.isfile(os.path.join(runtime, "tcl", n, "init.tcl"))]
    if not tcl:
        absent.append("runtime/tcl: libreria di Tcl")
    check("pacchetto: programma, PatchSmith.exe e Python portatile con Tkinter e ._pth (mancano: %s)" % absent,
              not absent and len(pth) == 1)
    unwanted = [n for n in project.USER_DATA + ("test", "_temporanei") if os.path.exists(os.path.join(folder, n))]
    components = [n for n in os.listdir(os.path.join(folder, "components")) if n != "plugin"] + \
        [n for n in os.listdir(os.path.join(folder, "components", "plugin")) if not n.endswith(".cs")]
    lib = [n for n in ("test", "site-packages", "idlelib") if os.path.exists(os.path.join(runtime, "Lib", n))]
    check("niente dati di chi lo usa, prove, componenti scaricati o parti di Python inutili (%s)"
              % (unwanted + components + lib), not unwanted and not components and not lib)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    in_package = sum(len([f for f in file]) for r, c, file in os.walk(folder) if "__pycache__" not in r)
    check("zip: solo la cartella PatchSmith, senza __pycache__, con tutti i file del pacchetto (%d)" % len(names),
              all(n.startswith("PatchSmith/") for n in names) and not any("__pycache__" in n for n in names)
              and len(names) == in_package and "PatchSmith/runtime/pythonw.exe" in names)
    r = subprocess.run([os.path.join(runtime, "python.exe"), "-I", os.path.join(folder, "smith.py"), "-h"],
                       capture_output=True, timeout=60, cwd=base)
    check("smith.py con il Python portatile", r.returncode == 0 and b"pacchetto" in r.stdout)
    # PatchSmith.exe: with an installed Python it still chooses the package's one
    with open(os.path.join(folder, "PatchSmith.pyw"), "w", encoding="utf-8") as f:
        f.write("import os, sys\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'partito.txt'), 'w')"
                ".write(sys.executable)\n")
    started_file = os.path.join(folder, "partito.txt")
    subprocess.run([os.path.join(folder, "PatchSmith.exe")], timeout=30)
    for _ in range(60):
        if os.path.isfile(started_file):
            break
        time.sleep(0.25)
    used_path = open(started_file, encoding="utf-8").read() if os.path.isfile(started_file) else None
    check("PatchSmith.exe apre PatchSmith con il Python portatile (%s)" % used_path,
              used_path and os.path.normcase(used_path) == os.path.normcase(os.path.join(runtime, "pythonw.exe")))
    # a package someone has worked in: its data stays
    os.makedirs(os.path.join(folder, "profiles"))
    try:
        package.prepare(os.path.join(base, "rilascio"), log=lambda _r: None)
        was_refused = False
    except package.PackageError:
        was_refused = True
    try:
        package.prepare(os.path.join(package.PROGRAM, "rilascio"), log=lambda _r: None)
        inside = False
    except package.PackageError:
        inside = True
    check("un pacchetto con dei dati dentro non si cancella; mai dentro la cartella del programma",
              was_refused and os.path.isdir(os.path.join(folder, "profiles")) and inside
              and not os.path.exists(os.path.join(package.PROGRAM, "rilascio")))
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
