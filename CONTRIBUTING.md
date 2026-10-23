# Contributing to PatchSmith

Thanks for your interest! Bug reports, games that do not work, translations of the interface and code are all welcome.

- **A game that does not work, a bug, an idea:** open an [issue](https://github.com/shottocode/PatchSmith/issues) (English or Italian) and attach the zip made by **Create report**: it does not contain your DeepL key or email.
- **Questions:** [Discussions](https://github.com/shottocode/PatchSmith/discussions).
- **Security problems:** privately, with [Report a vulnerability](https://github.com/shottocode/PatchSmith/security/advisories/new).

## Italian left in the code

Names, comments, docstrings, interface texts, file names, the command line and the documentation are in English; the Italian interface comes from `patchsmith/ui_language/it.json` (English text → Italian text). PatchSmith started as a personal Italian project, so a few things are still in Italian (the [glossary](#glossary) below covers the words you will meet most often):

- **The keys saved in user files** (settings, profiles, translation memory: `"cartella_gioco"`, `"non_tradurre"`, `"lingua_destinazione"`...) and the codes stored in them (outcomes like `"GIOCO_APERTO"`, actions like `"riapplica"`). They are a file format: do not rename them.
- **Markers written into games**, such as the first comment of `patchsmith/injector_live.js` ("Patchsmith - traduzione dal vivo"): PatchSmith recognizes its own files by them, so they must never change.

Write new names, comments and docstrings in English.

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

## Glossary

The Italian words you will meet most often in the data keys and the stored codes.

| Italian | English | Notes |
|---|---|---|
| `gioco`, `motore` | game, engine | |
| `cartella`, `percorso`, `radice` | folder, path, root | `cartella_gioco`: the game's folder |
| `file`, `dati`, `voce`, `voci` | file, data, entry, entries | |
| `profilo` | game profile | JSON in `profiles/`: which files and texts of a game PatchSmith translates |
| `memoria` | translation memory | JSON in `memory/`: original text → translation, per game; `memoria comune`: shared by all games |
| `testo`, `testi`, `riga`, `righe` | text, texts, line, lines | |
| `lingua`, `da` / `a` | language, from / to | `lingua_destinazione`: target language |
| `originale`, `tradotto`, `traduzione` | original, translated, translation | `rimasti in originale`: left untranslated |
| `traduttore`, `backend`, `modello` | translator, translation backend, model | `traduttore locale`: Ollama + TranslateGemma on the user's PC |
| `traduci`, `applica`, `riapplica`, `ripristina` | translate, apply, reapply, restore | the main operations |
| `rileva`, `rilevamento`, `scansiona` | detect, detection, scan | |
| `segnaposto` | placeholder | `%s`, `{0}`, `<color>`: protected during translation |
| `caratteri`, `lettere` | characters, letters | `caratteri mancanti`: letters the game's fonts cannot draw |
| `backup`, `manifesto`, `impronta` | backup, manifest, fingerprint (hash) | |
| `controllo`, `controlla` | after-play check, check | `problema`, `rimedio`, `sicuro`: problem found, its fix, safe to apply on its own; `segnale`: what the check saw, so the same problem is not reported twice |
| `esito` | outcome | the result of an operation as the interface shows it (`Esito`) |
| `correzioni` | fixes made by hand | the **Fix translations…** window |
| `scartate`, `scartato` | rejected | translations that failed the checks |
| `automatiche` | machine translations | as opposed to fixes made by hand (`a mano`) |
| `dal vivo` | live | translating new lines while the game runs |
| `registro`, `rapporto` | log, report | **Create report** |
| `impostazioni` | settings | `settings.json` |
| `componenti` | downloaded components | BepInEx, XUnity.AutoTranslator, Ollama and its models |
| `servizio` | service | the small local server that translates new lines for XUnity |
| `configuratore`, `leva` | configurator, lever | reversible changes to a game's options (for example its language) |
| `scheda` | game sheet | knowledge about one specific game (`games.py`) |
| `lingua ufficiale` | official language | the game already has the target language |
| `preliminari` | preliminary checks | before starting: game closed, disk space, translator ready |
| `interfaccia`, `finestra`, `pulsante`, `etichetta` | interface, window/dialog, button, label | |
| `tema`, `suoni`, `musica` | theme, sounds, music | |
| `prova`, `finto` | test (or trial), fake | `backend finto`: the offline test translator |
| `nome`, `codice`, `chiave`, `valore`, `elenco` | name, code, key, value, list | |
| `stato`, `uscita`, `inizio`, `fine` | state/status, output/exit, start, end | |
| `nuovo`, `vecchio`, `mancanti`, `trovati` | new, old, missing, found | |
| `leggi`, `scrivi`, `salva`, `trova`, `crea` | read, write, save, find, create | |

## License

PatchSmith is released under the [MIT License](LICENSE): by contributing you agree that your contribution is released under the same license.
