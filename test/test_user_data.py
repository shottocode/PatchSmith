"""User data folder (PATCHSMITH_DATA) separate from the program.

Usage: python -I test/test_user_data.py <empty_temp_folder>
Covers:
- with PATCHSMITH_DATA, game profiles, translation memory, settings, translator status, language and configurator
  logs, and saves go there; downloaded components, plugin, rules and smith.py stay next to the program;
- without the variable everything stays as before (next to the program), also for whoever changes
  PROJECT_FOLDER;
- command line: `smith auto`, `status` and `restore` on a fake web game with the data in the test folder;
- the translator command for XUnity written into the game always uses smith.py and the program's folder;
- the interface settings are saved even if the data folder does not exist yet.
The child processes only compute paths, except the command line, which runs only if the variable is honored.
"""
import hashlib
import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_web_game  # noqa: E402
from patchsmith import adapter_unity, gui, project  # noqa: E402

OUTCOMES = []
SMITH = os.path.join(project.PROJECT_FOLDER, "smith.py")

# prints the paths PatchSmith would use (no writing)
PATHS = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from patchsmith import (adapter_unity, auto as automatic, backend, components,
                        configurator, gui, limiter,
                        official_language, ollama, project,
                        detection)
profile = {"name": "gioco", "target_language": "it"}
print(json.dumps({
    "data": {
        "profiles": automatic.profiles_folder(),
        "memory": project.memory_path(profile),
        "shared": project.shared_path("en", "it"),
        "unity_memory": adapter_unity._memory_folder("gioco"),
        "settings": gui._settings(),
        "services": limiter.state_path(),
        "configurations": configurator._log_file(sys.argv[1]),
        "languages": official_language._log_file(sys.argv[1]),
        "logs": project.data_folder("logs"),
    },
    "program": {
        "components": components.folder(),
        "plugin": adapter_unity.SERVICE_SOURCE,
        "ollama": ollama.folder(),
        "rules": detection.DATA_FOLDER,
    },
    # only with the test data: the user's real credentials are not read
    "deepl_key": backend.credentials()["deepl_key"] if project.DATA_ROOT else None,
}))
"""


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def inside(path, folder):
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return path.startswith(folder.rstrip("\\/") + os.sep)


def environment(base, data=None):
    """Environment of the child processes: test user folders, PATCHSMITH_DATA only if given."""
    test_env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PATCHSMITH_", "OLLAMA_"))}
    for env_var, rel in (("LOCALAPPDATA", "Local"), ("APPDATA", "Roaming"), ("TEMP", "Temp"), ("TMP", "Temp"),
                           ("USERPROFILE", "user")):
        test_env[env_var] = os.path.join(base, "env", rel)
        os.makedirs(test_env[env_var], exist_ok=True)
    if data:
        test_env["PATCHSMITH_DATA"] = data
    return test_env


def paths(base, data):
    r = subprocess.run([sys.executable, "-I", "-c", PATHS, project.PROJECT_FOLDER], env=environment(base, data),
                       cwd=base, capture_output=True, timeout=120)
    if r.returncode:
        print(r.stderr.decode("utf-8", "replace")[-1500:])
        return None
    return json.loads(r.stdout.decode("utf-8"))


def smith(base, data, *arguments):
    r = subprocess.run([sys.executable, "-I", SMITH] + list(arguments), env=environment(base, data), cwd=base,
                       capture_output=True, timeout=600)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def fingerprints(folder):
    entries = {}
    for r, _, fs in os.walk(folder):
        for f in fs:
            with open(os.path.join(r, f), "rb") as g:
                entries[os.path.relpath(os.path.join(r, f), folder)] = hashlib.sha256(g.read()).hexdigest()
    return entries


def test_paths(base):
    data = os.path.join(base, "data-variable")
    os.makedirs(data)
    with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"deepl_key": "test-key"}, f)
    with_data = paths(base, data)
    respected = bool(with_data) and all(inside(p, data) for p in with_data["data"].values())
    check("PATCHSMITH_DATA: all the user's data in its folder", respected)
    if with_data and not respected:
        print("   outside:", {k: p for k, p in with_data["data"].items() if not inside(p, data)})
    check("PATCHSMITH_DATA: settings (DeepL key) read from there",
              with_data and with_data["deepl_key"] == "test-key")
    check("PATCHSMITH_DATA: components, plugin, local translator and rules stay next to the program",
              with_data and all(inside(p, project.PROJECT_FOLDER) for p in with_data["program"].values()))
    unlabeled = paths(base, None)
    check("without PATCHSMITH_DATA: the data stays next to the program, as before",
              unlabeled and all(inside(p, project.PROJECT_FOLDER) for p in unlabeled["data"].values())
              and os.path.dirname(unlabeled["data"]["profiles"]) == project.PROJECT_FOLDER)

    # whoever moves PROJECT_FOLDER (old test scripts) also moves the data, if the variable is not set
    saved_items = project.PROJECT_FOLDER, project.DATA_ROOT
    try:
        project.PROJECT_FOLDER, project.DATA_ROOT = os.path.join(base, "elsewhere"), None
        check("DATA_ROOT empty: the data follows PROJECT_FOLDER",
                  project.data_folder("memory") == os.path.join(base, "elsewhere", "memory"))
    finally:
        project.PROJECT_FOLDER, project.DATA_ROOT = saved_items
    return respected


def test_command_line(base):
    data = os.path.join(base, "data-cli")
    game = os.path.join(base, "games", "Lantern Keeper")
    rel = fake_web_game.create(game)
    originals = fingerprints(game)
    code, output = smith(base, data, "auto", game, "--backend", "fake", "--a", "it")
    profiles = os.listdir(os.path.join(data, "profiles")) if os.path.isdir(os.path.join(data, "profiles")) else []
    check("smith auto: translated, game profile and memory in the data folder",
              code == 0 and profiles == ["lantern-keeper.json"]
              and os.path.exists(os.path.join(data, "memory", "lantern-keeper.it.prova.json"))
              and os.path.exists(os.path.join(game, *rel.split("/")) + project.BACKUP_SUFFIX))
    if code:
        print("\n".join(output.strip().splitlines()[-10:]))
    code, output = smith(base, data, "status", "lantern-keeper", "--esempi", "1")
    check("smith status: the game profile is found by name in the data folder",
              code == 0 and "Testi unici" in output)
    code, _ = smith(base, data, "restore", game)
    check("smith restore: game identical to the original", code == 0 and fingerprints(game) == originals)


def test_xunity_service(base):
    """The command written into the game: smith.py and the program's folder, even with the data elsewhere."""
    game = os.path.join(base, "unity")
    os.makedirs(os.path.join(game, "BepInEx", "config"))
    profile = os.path.join(base, "data-service", "profiles", "gioco.json")
    compile_plugin = adapter_unity._compile_plugin
    saved = project.DATA_ROOT
    try:
        adapter_unity._compile_plugin = lambda *_: None  # no compiler: only the command matters here
        project.DATA_ROOT = os.path.join(base, "data-service")
        adapter_unity.enable_service(game, {"data_folder": game}, profile, "game")
    finally:
        adapter_unity._compile_plugin = compile_plugin
        project.DATA_ROOT = saved
    with open(os.path.join(game, *adapter_unity.SERVICE_CONFIG.split("/")), encoding="utf-8") as f:
        lines = dict(r.split("=", 1) for r in f.read().splitlines() if "=" in r and not r.startswith("#"))
    check("service for XUnity: smith.py and the program's folder, game profile with its path",
              ('"%s"' % SMITH) in lines.get("Arguments", "") and ('--profile "%s"' % profile) in lines["Arguments"]
              and lines.get("Folder") == project.PROJECT_FOLDER)


def test_gui_settings(base):
    """_save_settings with a data folder that does not exist yet (first time with PATCHSMITH_DATA)."""
    saved = project.DATA_ROOT
    project.DATA_ROOT = os.path.join(base, "data-gui", "new")
    try:
        mock = types.SimpleNamespace(
            _chosen="local", folder=types.SimpleNamespace(get=lambda: "C:/Games/Fake"),
            src_lang=types.SimpleNamespace(get=lambda: "auto"), _target_lang=lambda: "it", theme_choice="auto",
            deepl_key="", email_mymemory="", _model_key=lambda: "4b")
        gui.App._save_settings(mock)
        with open(os.path.join(project.DATA_ROOT, "settings.json"), encoding="utf-8") as f:
            saved_count = json.load(f)
        check("interface: settings saved in the data folder (created if missing)",
                  saved_count.get("folder") == "C:/Games/Fake" and saved_count.get("backend") == "local")
    except OSError as e:
        check("interface: settings saved in the data folder (created if missing): %s" % e, False)
    finally:
        project.DATA_ROOT = saved


def test_english_names(base):
    """Data with the Italian names from before 0.1.0 switches to the English ones (moved, never copied or deleted);
    until a folder is moved the old one is used; a game that asks for the game profile with the old path finds it
    again."""
    from patchsmith import cli
    root = os.path.join(base, "old-names")
    old_files = {"profili/gioco.json": "{}", "memoria/gioco.it.json": '{"a": "b"}',
              "memoria/copie/old/x.json": "{}",
              "registri/op.log": "log", "impostazioni.json": '{"tema": "scuro"}', "stato_servizi.json": "{}",
              "componenti/ollama/ollama.exe": "exe", "componenti/plugin/Patchsmith.UIToolkit.dll": "dll",
              "components/plugin/PatchsmithServizio.cs": "cs"}  # (components/plugin is already there: the new sources)
    for rel, text in old_files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    saved_items = project.PROJECT_FOLDER, project.DATA_ROOT
    project.PROJECT_FOLDER, project.DATA_ROOT = root, None
    try:
        before = project.data_folder("profiles", "gioco.json")  # not moved yet: the old one
        moved_names = project.migrate_data()

        def read_file(rel):
            p = os.path.join(root, *rel.split("/"))
            return open(p, encoding="utf-8").read() if os.path.isfile(p) else None
        new_items = {"profiles/gioco.json": "{}", "memory/gioco.it.json": '{"a": "b"}',
                 "memory/copies/old/x.json": "{}",
                 "logs/op.log": "log", "services.json": "{}",
                 # settings.json with the English names (migrate_formats), the original kept next to it
                 "settings.json": '{\n  "theme": "dark"\n}', "settings.json.italian-keys": '{"tema": "scuro"}',
                 "components/ollama/ollama.exe": "exe", "components/plugin/Patchsmith.UIToolkit.dll": "dll",
                 "components/plugin/PatchsmithServizio.cs": "cs"}
        leftovers = [n for n in ("profili", "memoria", "registri", "componenti", "impostazioni.json") if
                   os.path.exists(os.path.join(root, n))]
        check("English names: data moved (%s), nothing lost, components merged, no old folder (%s)"
                  % (sorted(moved_names), leftovers), all(read_file(r) == t for r, t in new_items.items())
                  and not leftovers and before == os.path.join(root, "profili", "gioco.json"))
        again = project.migrate_data()
        located = cli._profile(os.path.join("C:\\", "old", "PatchSmith", "profili", "gioco.json"))
        check("English names: nothing to do the second time; the game profile asked for with the old path "
                  "(from the command inside an already translated game) is found", again == []
                  and located == os.path.join(root, "profiles", "gioco.json"))
    finally:
        project.PROJECT_FOLDER, project.DATA_ROOT = saved_items


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "data_folder")  # this process too: never the real data
    if test_paths(base):
        test_command_line(base)
    else:  # if the variable were not honored, the command line would write among the real data
        check("command line not tested: PATCHSMITH_DATA not honored", False)
    test_xunity_service(base)
    test_gui_settings(base)
    test_english_names(base)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
