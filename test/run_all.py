"""Runs all the tests (test/test_*.py), each with python -I, with PatchSmith's data in a temporary folder.

Usage: python -I test/run_all.py [--work FOLDER] [--safety COPY JSON_FILE] [--keep] [test ...]
- each test gets an empty folder of its own (tests/<name>), holding its own PatchSmith data (game profiles,
  translation memory, settings, logs...: the PATCHSMITH_DATA folder) and its own %LOCALAPPDATA%, %APPDATA%, %TEMP%
  and user folder: the games' real options, crashes and saves are not touched, and a test does not see what
  another one left behind;
- each test's output goes to logs/<name>.txt; a test that runs longer than MAX_SECONDS is stopped, together
  with the processes it started;
- test_gui runs on a fake web game created here (PatchSmith's window opens for a few seconds), with some
  pre-existing settings that the test must put back as they were;
- test_safety needs a COPY of a game (--safety <copy> <relative file.json>): first
  `smith.py auto <copy> --backend fake` is run; without the copy the test is skipped;
- at the end it checks that PatchSmith's real data (game profiles, translation memory, settings...) has not changed;
- the work folder is deleted if everything goes well (with --keep, or if you chose it yourself with --work, it
  stays).
With some names (e.g. test_unity test_xna) it runs only those. Exit code: 0 if all the tests run pass and the real
data has not changed. The runner's functions are checked by test/test_run_all.py.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_web_game  # noqa: E402
from patchsmith import project  # noqa: E402

NO_ARGUMENTS = {"test_cjk", "test_placeholders"}
MAX_SECONDS = 900


def tests(choices):
    names = sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    choices = [os.path.basename(s)[:-3] if s.endswith(".py") else s for s in choices]  # also "test_unity.py"
    unknowns = [n for n in choices if n not in names]
    if unknowns:
        raise SystemExit("Unknown tests: %s" % ", ".join(unknowns))
    return [n for n in names if not choices or n in choices]


def snapshot():
    """Size and date of every file of the user's real data (next to the program and, if set, in the
    PATCHSMITH_DATA of whoever runs the tests)."""
    roots = {project.PROJECT_FOLDER}
    if project.DATA_ROOT:
        roots.add(project.DATA_ROOT)
    entries = {}
    for root in roots:
        for name in project.USER_DATA + project.USER_DATA_BEFORE:
            p = os.path.join(root, name)
            paths = [p] if os.path.isfile(p) else [os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs]
            for q in paths:
                try:
                    s = os.stat(q)
                except OSError:
                    continue
                entries[q] = (s.st_size, s.st_mtime_ns)
    return entries


def environment(folder):
    """Environment variables of a test: PatchSmith data and Windows user folders all of its own, inside its folder
    (without the PATCHSMITH_* and OLLAMA_* of whoever runs the tests)."""
    test_env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PATCHSMITH_", "OLLAMA_"))}
    folders = {"PATCHSMITH_DATA": "data", "LOCALAPPDATA": "user/AppData/Local", "APPDATA": "user/AppData/Roaming",
               "TEMP": "user/Temp", "TMP": "user/Temp", "USERPROFILE": "user"}
    for env_var, rel in folders.items():
        test_env[env_var] = os.path.join(folder, *rel.split("/"))
        os.makedirs(test_env[env_var], exist_ok=True)
    # the tests check the texts of the code (test_ui_language chooses Italian)
    test_env["PATCHSMITH_INTERFACE"] = "en"
    return test_env


def _stop(process):
    """Stops a test and the processes it started (a test server left running must not block the rest)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    process.kill()
    process.wait()


def run_command(command, test_env, cwd, log_file, maximum=None):
    """Runs a command with its output written to the log: a file, not a pipe, so a process started by the test and
    left running does not block the reading. (code, output, seconds); code None = stopped because it ran too
    long."""
    maximum = maximum or MAX_SECONDS
    start = time.time()
    with open(log_file, "ab") as f:
        f.write(("$ %s\n" % " ".join(command)).encode("utf-8"))
        f.flush()
        src_lang = f.tell()
        process = subprocess.Popen(command, cwd=cwd, env=test_env, stdin=subprocess.DEVNULL, stdout=f,
                                    stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=maximum)
        except subprocess.TimeoutExpired:
            _stop(process)
            code = None
    with open(log_file, "rb") as f:
        f.seek(src_lang)
        output = f.read().decode("utf-8", "replace")
    if code is None:
        note = "\n(stopped: over %d s)\n" % maximum
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(note)
        output += note
    return code, output, time.time() - start


def main(argv=None):
    ap = argparse.ArgumentParser(description="Runs all of PatchSmith's tests without touching the real data.")
    ap.add_argument("tests", nargs="*", help="only these (e.g. test_unity)")
    ap.add_argument("--work", "--lavoro", dest="work", metavar="FOLDER",
                    help="work folder (empty or to be created); default: a temporary folder")
    ap.add_argument("--safety", "--sicurezza", dest="safety", nargs=2, metavar=("COPY", "JSON_FILE"),
                    help="copy of a game and relative JSON file for test_safety (the copy is modified)")
    ap.add_argument("--keep", "--tieni", dest="keep", action="store_true",
                    help="do not delete the work folder at the end")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    items = tests(a.tests)
    if a.work:
        work = os.path.abspath(a.work)
        if os.path.exists(work) and os.listdir(work):
            raise SystemExit("The work folder must be empty: %s" % work)
        os.makedirs(work, exist_ok=True)
    else:
        work = tempfile.mkdtemp(prefix="patchsmith-tests-")
    logs = os.path.join(work, "logs")
    os.makedirs(logs)
    print("Work folder: %s (each test in tests\\<name>, with its test data in tests\\<name>\\data)" % work,
          flush=True)
    before = snapshot()

    outcomes = {}  # name -> (state, seconds, output); state: "OK", "ERROR", "SKIPPED"
    for name in items:
        log_file = os.path.join(logs, name + ".txt")
        folder = os.path.join(work, "tests", name)
        script = os.path.join(HERE, name + ".py")
        if name == "test_safety" and not a.safety:
            outcomes[name] = ("SKIPPED", 0, "needs a copy of a game: --safety <copy> <file.json>")
            print("SKIPPED  %-28s needs a copy of a game: --safety <copy> <file.json>" % name, flush=True)
            continue
        os.makedirs(folder)
        test_env = environment(folder)
        if name == "test_gui":
            game = os.path.join(folder, "Lantern Keeper")
            fake_web_game.create(game)
            # pre-existing settings, as for someone who already uses PatchSmith: the test must put them back as
            # they were
            with open(os.path.join(test_env["PATCHSMITH_DATA"], "settings.json"), "w", encoding="utf-8") as f:
                json.dump({"version": 2, "note": "settings from before the test"}, f)
            arguments = [game]
        elif name == "test_safety":
            copy_dir = os.path.abspath(a.safety[0])
            code, output, _ = run_command([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "smith.py"),
                                        "auto", copy_dir, "--backend", "fake"], test_env, folder, log_file)
            if code != 0:
                outcomes[name] = ("ERROR", 0, output)
                print("ERROR    %-28s prerequisite `smith auto` failed (code %s)" % (name, code), flush=True)
                continue
            arguments = [copy_dir, a.safety[1]]
        elif name in NO_ARGUMENTS:
            arguments = []
        else:
            arguments = [os.path.join(folder, "base")]
        code, output, seconds = run_command([sys.executable, "-I", script] + arguments, test_env, folder, log_file)
        state = "OK" if code == 0 else "ERROR"
        outcomes[name] = (state, seconds, output)
        note = "" if code == 0 else "  (stopped: over %d s)" % MAX_SECONDS if code is None else \
            "  (code %s)" % code
        print("%-8s %-28s %5.1f s%s" % (state, name, seconds, note), flush=True)

    after = snapshot()
    changed_files = sorted((set(before) ^ set(after)) | {p for p in set(before) & set(after) if before[p] != after[p]})
    failed = [n for n, (state, _, _) in outcomes.items() if state == "ERROR"]
    for name in failed:
        lines = outcomes[name][2].strip().splitlines()
        print("\n--- %s (last lines; everything in %s)" % (name, os.path.join(logs, name + ".txt")))
        print("\n".join(lines[-15:]))
    if changed_files:
        print("\nWARNING: some of PatchSmith's real data changed during the tests (if you were using PatchSmith or "
              "playing with the live translation in the meantime, that may have been it):")
        print("\n".join("  " + p for p in changed_files[:20]))
    ran = [n for n, (state, _, _) in outcomes.items() if state != "SKIPPED"]
    skipped = [n for n, (state, _, _) in outcomes.items() if state == "SKIPPED"]
    print("\nRESULT: %d of %d tests passed%s; real data %s" % (
        len(ran) - len(failed), len(ran), " (skipped: %s)" % ", ".join(skipped) if skipped else "",
        "CHANGED" if changed_files else "unchanged"))
    ok = not failed and not changed_files
    if ok and not a.keep and not a.work:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print("Work folder kept: %s" % work)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
