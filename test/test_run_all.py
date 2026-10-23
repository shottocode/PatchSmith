"""Checks on the test runner (test/run_all.py), without running the real tests.

Usage: python -I test/test_run_all.py <empty_temp_folder>
Covers:
- each test has its own data (PATCHSMITH_DATA) and its own user folders, inside its folder, without the
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
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"])
with open(sys.argv[1], "w") as f:
    f.write(str(child.pid))
print("started", flush=True)
time.sleep(120)
"""


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def inside(path, folder):
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return path.startswith(folder.rstrip("\\/") + os.sep)


def test_environment(base):
    os.environ["PATCHSMITH_DATA"] = os.path.join(base, "runner-data")
    os.environ["OLLAMA_HOST"] = "127.0.0.1:1"
    singular = run_all.environment(os.path.join(base, "tests", "test_one"))
    two = run_all.environment(os.path.join(base, "tests", "test_two"))
    env_vars = ("PATCHSMITH_DATA", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE")
    check("environment: the user's data and folders inside the test's folder, already created",
              all(inside(singular[v], os.path.join(base, "tests", "test_one")) and os.path.isdir(singular[v])
                  for v in env_vars))
    check("environment: two tests share neither data nor user folders",
              all(os.path.normcase(singular[v]) != os.path.normcase(two[v]) for v in env_vars))
    check("environment: none of the PATCHSMITH_* and OLLAMA_* of whoever runs the tests",
              "OLLAMA_HOST" not in singular and not inside(singular["PATCHSMITH_DATA"], os.environ["PATCHSMITH_DATA"])
              and not [k for k in singular if k.upper().startswith("PATCHSMITH_")
                       and k not in ("PATCHSMITH_DATA", "PATCHSMITH_INTERFACE")]
              and singular.get("PATCHSMITH_INTERFACE") == "en")
    del os.environ["PATCHSMITH_DATA"], os.environ["OLLAMA_HOST"]


def test_run(base):
    folder = os.path.join(base, "run")
    os.makedirs(folder)
    test_env = run_all.environment(folder)
    log_file = os.path.join(folder, "log.txt")
    code, output, _ = run_all.run_command(
        [sys.executable, "-I", "-c", "import os, sys; print('data=' + os.environ['PATCHSMITH_DATA']); sys.exit(3)"],
        test_env, folder, log_file)
    with open(log_file, encoding="utf-8") as f:
        written = f.read()
    check("run: exit code, output and log", code == 3 and "data=" + test_env["PATCHSMITH_DATA"] in output
              and "data=" in written and written.startswith("$ "))

    pid_file = os.path.join(folder, "child.pid")
    script = os.path.join(folder, "hung.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(HUNG)
    start = time.time()
    code, output, _ = run_all.run_command([sys.executable, "-I", script, pid_file], test_env, folder, log_file,
                                            maximum=3)
    duration = time.time() - start
    check("test too long: stopped, the runner is not blocked (%.0f s)" % duration,
              code is None and duration < 60 and "started" in output and "(stopped: over 3 s)" in output)
    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read())
    except (OSError, ValueError):
        pid = None
    for _ in range(50):
        if pid is None or not xunity_service.process_alive(pid):
            break
        time.sleep(0.1)
    check("test too long: the processes it started are stopped too",
              pid is not None and not xunity_service.process_alive(pid))


def test_snapshot(base):
    saved_items = project.PROJECT_FOLDER, project.DATA_ROOT
    root = os.path.join(base, "program")
    for rel, text in (("profiles/game.json", "{}"), ("memory/game.it.json", "{}"), ("settings.json", "{}"),
                       ("components/other.txt", "x")):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    try:
        project.PROJECT_FOLDER, project.DATA_ROOT = root, None
        before = run_all.snapshot()
        check("snapshot: the user's data yes, components no",
                  os.path.join(root, "profiles", "game.json") in before
                  and os.path.join(root, "settings.json") in before
                  and not any("components" in p for p in before))
        with open(os.path.join(root, "memory", "game.it.json"), "w", encoding="utf-8") as f:
            f.write('{"a": "b"}')
        with open(os.path.join(root, "memory", "new.it.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.remove(os.path.join(root, "settings.json"))
        after = run_all.snapshot()
        changed_files = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
        check("snapshot: it notices a file changed, added and removed", changed_files == {
            os.path.join(root, "memory", "game.it.json"), os.path.join(root, "memory", "new.it.json"),
            os.path.join(root, "settings.json")})
    finally:
        project.PROJECT_FOLDER, project.DATA_ROOT = saved_items


def test_names():
    all_items = run_all.tests([])
    check("names: also with .py", run_all.tests(["test_unity.py"]) == ["test_unity"])
    try:
        run_all.tests(["test_that_does_not_exist"])
        unknown = False
    except SystemExit:
        unknown = True
    check("names: an unknown test is reported", unknown)
    unlabeled = set()  # the tests that do not read arguments from the command line
    for name in all_items:
        with open(os.path.join(HERE, name + ".py"), encoding="utf-8") as f:
            if not re.search(r"sys\.argv\[", f.read()):
                unlabeled.add(name)
    check("tests without arguments: the runner's list is right (%s)" % ", ".join(sorted(unlabeled)),
              unlabeled == run_all.NO_ARGUMENTS)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "data")  # to be safe: these tests do not write data
    test_environment(base)
    test_run(base)
    test_snapshot(base)
    test_names()
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
