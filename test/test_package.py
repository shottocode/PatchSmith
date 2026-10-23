"""Package to publish (package.py): folder and zip with PatchSmith.exe and the portable Python.

Usage: python -I test/test_package.py <test_folder_that_does_not_exist>
- the package with the program, PatchSmith.exe and runtime/ (Python with Tkinter and the ._pth file), without the data
  of whoever uses PatchSmith, the tests and the downloaded components; the zip with only the PatchSmith folder, without
  __pycache__;
- the portable Python starts on its own (prepara already tests it) and runs smith.py;
- PatchSmith.exe chooses the portable Python even with an installed Python (here a test PatchSmith.pyw);
- a used package (with data inside) is not deleted; the package does not go inside the program's folder.
"""
import os
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import package as pacchetto, project as progetto  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    if not sys.platform.startswith("win"):
        print("Solo Windows: niente da provare.")
        return 0
    righe = []
    cartella, archivio = pacchetto.prepara(os.path.join(base, "rilascio"), log=righe.append)
    print("\n".join(righe))
    runtime = os.path.join(cartella, "runtime")
    attesi = ["PatchSmith.exe", "PatchSmith.pyw", "smith.py", "LEGGIMI.md", "README.md", "LICENSE", "patchsmith/gui.py",
              "patchsmith/launcher.cs", "docs/TECNICO.md", "docs/TECHNICAL.md", "patchsmith/icon/16.png",
              "patchsmith/ui_language/en.json", "data/rules.ini",
              "components/plugin/PatchsmithServizio.cs", "runtime/pythonw.exe", "runtime/DLLs/_tkinter.pyd",
              "runtime/Lib/tkinter/__init__.py", "runtime/LICENSE.txt"]
    mancano = [a for a in attesi if not os.path.isfile(os.path.join(cartella, *a.split("/")))]
    pth = [n for n in os.listdir(runtime) if n.endswith("._pth")]
    # the Tcl library: the tcl8.6 folder with init.tcl, or from Tcl 9 the libtcl9.x.zip zip
    tcl = [n for n in os.listdir(os.path.join(runtime, "tcl")) if n.startswith("libtcl") and n.endswith(".zip")
           or os.path.isfile(os.path.join(runtime, "tcl", n, "init.tcl"))]
    if not tcl:
        mancano.append("runtime/tcl: libreria di Tcl")
    controlla("pacchetto: programma, PatchSmith.exe e Python portatile con Tkinter e ._pth (mancano: %s)" % mancano,
              not mancano and len(pth) == 1)
    di_troppo = [n for n in progetto.DATI_UTENTE + ("test", "_temporanei") if os.path.exists(os.path.join(cartella, n))]
    componenti = [n for n in os.listdir(os.path.join(cartella, "components")) if n != "plugin"] + \
        [n for n in os.listdir(os.path.join(cartella, "components", "plugin")) if not n.endswith(".cs")]
    lib = [n for n in ("test", "site-packages", "idlelib") if os.path.exists(os.path.join(runtime, "Lib", n))]
    controlla("niente dati di chi lo usa, prove, componenti scaricati o parti di Python inutili (%s)"
              % (di_troppo + componenti + lib), not di_troppo and not componenti and not lib)
    with zipfile.ZipFile(archivio) as z:
        nomi = z.namelist()
    nel_pacchetto = sum(len([f for f in file]) for r, c, file in os.walk(cartella) if "__pycache__" not in r)
    controlla("zip: solo la cartella PatchSmith, senza __pycache__, con tutti i file del pacchetto (%d)" % len(nomi),
              all(n.startswith("PatchSmith/") for n in nomi) and not any("__pycache__" in n for n in nomi)
              and len(nomi) == nel_pacchetto and "PatchSmith/runtime/pythonw.exe" in nomi)
    r = subprocess.run([os.path.join(runtime, "python.exe"), "-I", os.path.join(cartella, "smith.py"), "-h"],
                       capture_output=True, timeout=60, cwd=base)
    controlla("smith.py con il Python portatile", r.returncode == 0 and b"pacchetto" in r.stdout)
    # PatchSmith.exe: with an installed Python it still chooses the package's one
    with open(os.path.join(cartella, "PatchSmith.pyw"), "w", encoding="utf-8") as f:
        f.write("import os, sys\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'partito.txt'), 'w')"
                ".write(sys.executable)\n")
    partito = os.path.join(cartella, "partito.txt")
    subprocess.run([os.path.join(cartella, "PatchSmith.exe")], timeout=30)
    for _ in range(60):
        if os.path.isfile(partito):
            break
        time.sleep(0.25)
    usato = open(partito, encoding="utf-8").read() if os.path.isfile(partito) else None
    controlla("PatchSmith.exe apre PatchSmith con il Python portatile (%s)" % usato,
              usato and os.path.normcase(usato) == os.path.normcase(os.path.join(runtime, "pythonw.exe")))
    # a package someone has worked in: its data stays
    os.makedirs(os.path.join(cartella, "profiles"))
    try:
        pacchetto.prepara(os.path.join(base, "rilascio"), log=lambda _r: None)
        rifiutato = False
    except pacchetto.ErrorePacchetto:
        rifiutato = True
    try:
        pacchetto.prepara(os.path.join(pacchetto.PROGRAMMA, "rilascio"), log=lambda _r: None)
        dentro = False
    except pacchetto.ErrorePacchetto:
        dentro = True
    controlla("un pacchetto con dei dati dentro non si cancella; mai dentro la cartella del programma",
              rifiutato and os.path.isdir(os.path.join(cartella, "profiles")) and dentro
              and not os.path.exists(os.path.join(pacchetto.PROGRAMMA, "rilascio")))
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return 1 if falliti else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
