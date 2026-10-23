# Contributing to PatchSmith

Thanks for your interest! Bug reports, games that do not work, translations of the interface and code are all welcome.

- **A game that does not work, a bug, an idea:** open an [issue](https://github.com/shottocode/PatchSmith/issues) (English or Italian) and attach the zip made by **Create report**: it does not contain your DeepL key or email.
- **Questions:** [Discussions](https://github.com/shottocode/PatchSmith/discussions).
- **Security problems:** privately, with [Report a vulnerability](https://github.com/shottocode/PatchSmith/security/advisories/new).

## Italian left in the code

Names, comments, docstrings, interface texts, the keys and codes of the saved data, the command line and the documentation are in English; the Italian interface comes from `patchsmith/ui_language/it.json` (English text → Italian text). PatchSmith started as a personal Italian project, so a few things are still in Italian, on purpose:

- **Files written by earlier versions** have Italian keys and codes (`"cartella_gioco"`, `"lingua_destinazione"`, `"GIOCO_APERTO"`...). `patchsmith/legacy.py` renames them when PatchSmith reads its own files (`legacy.load`, `legacy.upgrade`), so profiles, settings, check states and the files left in translated games keep working; at startup the profiles, records and settings are also rewritten with the English names (`project.migrate_formats`, a copy of each original stays next to it with `.italian-keys`). Read every new structured file through `legacy`, never translation memories (game text → translation: a game text can be any word).
- **Names of files and markers**: the suffixes of the data files (`.controllo.json`, `.problemi.json`, `.automatiche.json`, `.origine.json`, `.da_scrivere.json`, `.richieste.jsonl`), the files written into games (`.patchsmith-originale` backups, `patchsmith-dizionario.json`, `patchsmith-vivo.json`, `patchsmith-traduzione.*`, `patchsmith-servizio.cfg`, the GUID of the BepInEx plugin) and the first comment of `patchsmith/injector_live.js` ("Patchsmith - traduzione dal vivo"). PatchSmith finds its own files by them, in games translated by any version: they must never change. The same holds for the names of some logs (`logs/<date>-controllo.log`, `-interfaccia.log`) and for the prefixes of the signals that the after-play check remembers (`tolti:`, `sorgenti:`...): new names would make it redo the checks and fixes already done.
- **The two C# sources compiled ahead of time** (`patchsmith/launcher.cs` for `PatchSmith.exe`, `components/plugin/PatchsmithUIToolkit.cs`) still use some Italian names.

Write new names, comments, docstrings and data keys in English.

Issues and pull requests can be in English or Italian.

## Getting started

You need **Python 3.10+** with Tkinter. PatchSmith uses **only the standard library**: please do not add third-party packages.

```bash
python -I smith.py gui
```

- `python -I smith.py -h` lists the command-line commands (for example `python -I smith.py detect <game folder>`).
- Always run Python with `-I` (isolated mode), as the program and the tests do.
- [docs/TECHNICAL.md](docs/TECHNICAL.md) explains how every engine is handled, the after-play check, the data folders and every test.

| Where | What |
|---|---|
| `patchsmith/auto.py` | The automatic procedure behind **Analyze and translate**: detect, find the texts, translate, write |
| `patchsmith/detection.py`, `scan.py` | Engine detection (SteamDB rules + file signatures), files with readable text |
| `patchsmith/adapter_*.py` | One adapter per engine or file format (Unity, Unity Localization, Unreal 4, GameMaker, Godot 4, XNA, web) |
| `patchsmith/project.py` | Translation memory, backups, apply and restore |
| `patchsmith/backend.py`, `ollama.py`, `limiter.py` | Translators (local, DeepL, MyMemory, Google) and limits |
| `patchsmith/text.py`, `characters.py` | What counts as readable text, placeholder protection, letters the game's fonts lack |
| `patchsmith/checks.py`, `outcome.py` | The after-play check and the outcome of each operation, as shown by the interface |
| `patchsmith/gui.py`, `gui_corrections.py`, `ui_language.py` | The window, **Fix translations…** and the interface languages |
| `test/` | The tests (`test_*.py`) and fake games, translators and logs |

## Tests

```bash
python -I test/run_all.py
```

- Each test runs with its own empty data folder: tests never read or change your real profiles, memory or settings, and at the end the runner checks that they did not change.
- Only some tests: `python -I test/run_all.py test_unity test_xna`. `--keep` keeps the work folder, `--work <folder>` chooses it.
- The tests use fake games and fake translators: no real game, no network. `test_gui` opens PatchSmith's window for a few seconds.
- `test_safety` needs a copy of a real game (`--safety <copy> <file.json>`); without it, it is skipped.

Please add or update a test with every change, and run the whole suite before opening a pull request.

## Rules that every change keeps

- **Nothing is lost.** Every file of the game that PatchSmith changes is backed up first, and **Restore originals** must give back identical bytes and remove everything that was added. Where possible, add files instead of changing the game's own.
- **Hands off a running game:** nothing is written while the game is open.
- **No game executables are started**, not even by the tests.
- **No circumventing protections:** no breaking developers' encryption, no online games or games with anti-cheat, no support for cracked copies or emulators.
- **Downloads** come only from official releases, at pinned versions, with a verified SHA-256 (see `patchsmith/components.py`), and only after the user agrees.
- **Only translations are shared**, never game files.
- **Interface texts:** write the English text in the code and add its Italian translation to `patchsmith/ui_language/it.json` (`test_ui_language` checks that every text is there, with the same placeholders). Write singular and plural as two whole sentences, not sentences built from pieces: pieces cannot be translated.
- **Style:** lines up to 120 characters; keep each file's line endings (the repository stores files as they are).

## License

PatchSmith is released under the [MIT License](LICENSE): by contributing you agree that your contribution is released under the same license.
