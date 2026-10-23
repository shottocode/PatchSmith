"""Pacchetto da pubblicare (package.py): cartella e zip con PatchSmith.exe e il Python portatile.

Uso: python -I test/test_package.py <cartella_di_prova_che_non_esiste>
- il pacchetto con il programma, PatchSmith.exe e runtime/ (Python con Tkinter e il file ._pth), senza i dati di chi
  usa PatchSmith, le prove e i componenti scaricati; lo zip con la sola cartella PatchSmith, senza __pycache__;
- il Python portatile parte da solo (lo prova gia' prepara) e fa girare smith.py;
- PatchSmith.exe sceglie il Python portatile anche con un Python installato (qui un PatchSmith.pyw di prova);
- un pacchetto usato (con dei dati dentro) non si cancella; il pacchetto non va dentro la cartella del programma.
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
    # la libreria di Tcl: la cartella tcl8.6 con init.tcl, o da Tcl 9 lo zip libtcl9.x.zip
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
    # PatchSmith.exe: con un Python installato sceglie comunque quello del pacchetto
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
    # un pacchetto in cui qualcuno ha lavorato: i suoi dati restano
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
