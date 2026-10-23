"""Checks on the test runner (test/run_all.py), without running the real tests.

Usage: python -I test/test_run_all.py <empty_temp_folder>
Covers:
- each test has its own data (PATCHSMITH_DATI) and its own user folders, inside its folder, without the
  PATCHSMITH_* and OLLAMA_* variables of whoever runs the tests: a test does not see what another one left behind;
- the output goes into the log and the exit code is kept;
- a test that runs too long is stopped together with the processes it started, and the runner does not hang;
- the snapshot of the real data notices a changed, added or removed file;
- the test names (also with .py) and the list of tests without arguments, which must stay correct.
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import run_all  # noqa: E402
from patchsmith import project, xunity_service  # noqa: E402

OUTCOMES = []

# fake test that starts a child process (it inherits the output, like a test server) and then never ends
HUNG = r"""
import subprocess, sys, time
figlio = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"])
with open(sys.argv[1], "w") as f:
    f.write(str(figlio.pid))
print("avviato", flush=True)
time.sleep(120)
"""


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def inside(path, folder):
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return path.startswith(folder.rstrip("\\/") + os.sep)


def test_environment(base):
    os.environ["PATCHSMITH_DATI"] = os.path.join(base, "dati-di-chi-lancia")
    os.environ["OLLAMA_HOST"] = "127.0.0.1:1"
    singular = run_all.environment(os.path.join(base, "prove", "prova_uno"))
    two = run_all.environment(os.path.join(base, "prove", "prova_due"))
    env_vars = ("PATCHSMITH_DATI", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE")
    check("ambiente: dati e cartelle dell'utente dentro la cartella della prova, gia' create",
              all(inside(singular[v], os.path.join(base, "prove", "prova_uno")) and os.path.isdir(singular[v])
                  for v in env_vars))
    check("ambiente: due prove non condividono dati ne' cartelle dell'utente",
              all(os.path.normcase(singular[v]) != os.path.normcase(two[v]) for v in env_vars))
    check("ambiente: niente PATCHSMITH_* e OLLAMA_* di chi lancia le prove",
              "OLLAMA_HOST" not in singular and not inside(singular["PATCHSMITH_DATI"], os.environ["PATCHSMITH_DATI"])
              and not [k for k in singular if k.upper().startswith("PATCHSMITH_")
                       and k not in ("PATCHSMITH_DATI", "PATCHSMITH_INTERFACCIA")]
              and singular.get("PATCHSMITH_INTERFACCIA") == "en")
    del os.environ["PATCHSMITH_DATI"], os.environ["OLLAMA_HOST"]


def test_run(base):
    folder = os.path.join(base, "esegui")
    os.makedirs(folder)
    test_env = run_all.environment(folder)
    log_file = os.path.join(folder, "registro.txt")
    code, output, _ = run_all.run_command(
        [sys.executable, "-I", "-c", "import os, sys; print('dati=' + os.environ['PATCHSMITH_DATI']); sys.exit(3)"],
        test_env, folder, log_file)
    with open(log_file, encoding="utf-8") as f:
        written = f.read()
    check("esegui: codice di uscita, uscita e registro", code == 3 and "dati=" + test_env["PATCHSMITH_DATI"] in output
              and "dati=" in written and written.startswith("$ "))

    pid_file = os.path.join(folder, "figlio.pid")
    script = os.path.join(folder, "appesa.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(HUNG)
    start = time.time()
    code, output, _ = run_all.run_command([sys.executable, "-I", script, pid_file], test_env, folder, log_file,
                                            maximum=3)
    duration = time.time() - start
    check("prova troppo lunga: fermata, il runner non resta bloccato (%.0f s)" % duration,
              code is None and duration < 60 and "avviato" in output and "(fermata: oltre 3 s)" in output)
    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read())
    except (OSError, ValueError):
        pid = None
    for _ in range(50):
        if pid is None or not xunity_service.process_alive(pid):
            break
        time.sleep(0.1)
    check("prova troppo lunga: fermati anche i processi che aveva avviato",
              pid is not None and not xunity_service.process_alive(pid))


def test_snapshot(base):
    saved_items = project.PROJECT_FOLDER, project.DATA_ROOT
    root = os.path.join(base, "programma")
    for rel, text in (("profiles/gioco.json", "{}"), ("memory/gioco.it.json", "{}"), ("settings.json", "{}"),
                       ("components/altro.txt", "x")):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    try:
        project.PROJECT_FOLDER, project.DATA_ROOT = root, None
        before = run_all.snapshot()
        check("istantanea: dati dell'utente si', componenti no",
                  os.path.join(root, "profiles", "gioco.json") in before
                  and os.path.join(root, "settings.json") in before
                  and not any("components" in p for p in before))
        with open(os.path.join(root, "memory", "gioco.it.json"), "w", encoding="utf-8") as f:
            f.write('{"a": "b"}')
        with open(os.path.join(root, "memory", "nuovo.it.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.remove(os.path.join(root, "settings.json"))
        after = run_all.snapshot()
        changed_files = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
        check("istantanea: si accorge di un file cambiato, aggiunto e tolto", changed_files == {
            os.path.join(root, "memory", "gioco.it.json"), os.path.join(root, "memory", "nuovo.it.json"),
            os.path.join(root, "settings.json")})
    finally:
        project.PROJECT_FOLDER, project.DATA_ROOT = saved_items


def test_names():
    all_items = run_all.tests([])
    check("nomi: anche con .py", run_all.tests(["test_unity.py"]) == ["test_unity"])
    try:
        run_all.tests(["prova_che_non_esiste"])
        unknown = False
    except SystemExit:
        unknown = True
    check("nomi: una prova sconosciuta si segnala", unknown)
    unlabeled = set()  # the tests that do not read arguments from the command line
    for name in all_items:
        with open(os.path.join(HERE, name + ".py"), encoding="utf-8") as f:
            if not re.search(r"sys\.argv\[", f.read()):
                unlabeled.add(name)
    check("prove senza argomenti: l'elenco del runner e' giusto (%s)" % ", ".join(sorted(unlabeled)),
              unlabeled == run_all.NO_ARGUMENTS)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "dati")  # to be safe: these tests do not write data
    test_environment(base)
    test_run(base)
    test_snapshot(base)
    test_names()
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
