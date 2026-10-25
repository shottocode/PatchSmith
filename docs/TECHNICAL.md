# PatchSmith: technical details

*How PatchSmith works engine by engine, every check, the command line, the tests and how to add an adapter. To just use it, the [README](../README.md) is enough (in Italian: [LEGGIMI](../LEGGIMI.md)). Italian version of this document: [TECNICO.md](TECNICO.md).*

PatchSmith's source code is in English, interface texts and the keys of the saved data included. The files written by earlier versions, with Italian keys, are still read (`patchsmith/legacy.py`; at startup profiles, records and settings are rewritten with the English names, the original kept next to it as `.italian-keys`): see [CONTRIBUTING.md](../CONTRIBUTING.md). The Italian interface comes from `patchsmith/ui_language/it.json`.

## Interface language

The program's texts are written in English in the code; every other language is a file `patchsmith/ui_language/<code>.json`:

```json
{"name": "Italiano", "flag": {"vertical": ["#009246", "#ffffff", "#ce2b37"]}, "texts": {"Create report": "Crea rapporto", "Engine: %s": "Motore: %s"}}
```

- Translation happens when the window shows a text: buttons, labels, titles, menus, dialogs and log lines. An entry with `%s`, `%d`... applies to every line of that shape (`"Translated %d of %d"` translates "Translated 400 of 1654"), and the pieces of text inside are translated in turn. Whatever is not in the file stays in English; the log saved on disk is in English.
- **Adding a language** (e.g. French): `python -I smith.py interface --create fr --name Français --flag "{\"verticali\": [\"#0055a4\", \"#ffffff\", \"#ef4135\"]}"`. PatchSmith extracts from the code every text meant for the user and translates it with the local translator (the `%s`, `%d` placeholders are protected; entries already in the file are kept, so corrections made by hand are never lost). Then it is worth reviewing the window's texts by hand: machine translation gets single button words wrong most often.
- The flag is made of stripes (`{"vertical": [colors]}` or `{"horizontal": [colors]}`), or a 24x16 PNG image next to the file (`{"file": "fr.png"}`).
- At the first start the language is Windows' own if PatchSmith has it (today Italian), otherwise English. The language chosen with the flag button is saved and used from then on.

## The easy way: the graphical interface

Double-click **`PatchSmith.exe`** (or `PatchSmith.pyw`, or run `python -I smith.py gui`). `PatchSmith.exe` is a small launcher with the program's icon: it opens PatchSmith with the installed Python (the newest from 3.10 up) and, if Python is missing, explains what to install. PatchSmith compiles it itself with the C# compiler that every Windows has (source: `patchsmith/launcher.cs`), without downloading anything: if it is missing, it appears the first time `PatchSmith.pyw` is opened. It can be pinned to Start or copied to the Desktop as a shortcut.

The window's icon (a hammer over a folder, the game's) is drawn by `patchsmith/icon.py`, with no downloaded files: `python -I patchsmith/icon.py` rebuilds the images in `patchsmith/icon/` (a PNG for every size and `PatchSmith.ico`) and `PatchSmith.exe`.

1. **Browse…**: choose the game's folder (the one containing its executable). The field can only be filled this way: it cannot be typed into, so no wrong paths.
2. The languages usually need no change:
   - **From** is "Detect automatically": the program recognizes the game's language from its texts, offline. It looks at the script (Japanese, Chinese, Korean, Cyrillic…) and, for Latin-script languages, at the most frequent words.
   - **To** starts from the Windows language.

   The menus show names, for example "English (en)", and you can only choose from the list. A saved language no longer in the list goes back to the default one.
3. **Translator:** choose which one to use (see "Translators" below). The default is the **local translator**, even on a PC where it is not installed yet: next to it there is "to install" with the **Install…** button, which lists what it downloads and starts only after your yes. Next to it you choose the model: "Fast (4B)" or "Quality (12B, slower)". Without a suitable graphics card (graphics integrated in the processor, old cards or cards with less than 3.5 GB of memory) the local translator still works but uses the processor, even 10-20 times slower: the install window says so before downloading (and also warns with less than 8 GB of RAM), suggesting DeepL as an alternative. If you have not downloaded any model yet, PatchSmith suggests the one suited to your graphics card: the 12B with at least 8 GB of video memory, otherwise the 4B (the memory is read with the NVIDIA driver's `nvidia-smi`, from the Windows registry or from WMI; nothing is downloaded). After the install it translates a test sentence ("Start game") and the bottom line says whether it works, whether it uses the graphics card and how long it takes (if the test fails, a dialog explains why); if it only uses the processor, "slow: without a graphics card" appears next to the translator (installed and fast: no text). Free Google is labelled "unofficial" and, when you choose it, a dialog explains the blocks. For DeepL paste your key; for MyMemory you can give your email. Next to it you see whether the service is paused after a block.
4. Press **Analyze and translate**. Before starting, PatchSmith runs some quick checks (see "Before starting") and the **Stop** button interrupts at the first safe point: the translations made so far stay saved.
5. Start the game.

The other buttons:

| Button | What it does |
|---|---|
| **Fix translations…** | Opens the window with all the game's translations, search and the lists of texts worth checking (left untranslated, too long, suspicious…). Fix a line and press "Save and apply to the game": PatchSmith writes it into the game on its own (see "Fixing translations") |
| **Restore originals** | Puts back the original files, identical byte for byte. The translations stay saved: "Analyze and translate" brings them back right away, without translating them again (it only translates new texts, if a game update added some) |
| **Create report** (bottom right) | Creates on the Desktop a zip to send to whoever helps you, without the DeepL key and without the email (see below) |

**At the end of every operation** a dialog says how it went, with buttons for the next step:

| Outcome | What the dialog says | Buttons |
|---|---|---|
| Translated | How to see the translation in the game (for Unity: ALT+T, and who translates new lines while you play; if there is no translator right now it explains so), how many texts are translated | OK, Install the local translator (if new lines would stay untranslated) |
| Partly translated | How many texts stayed untranslated and why; what PatchSmith already retried on its own (see "Discarded texts and translations to redo") and the corrections made by hand that the game cannot display | Show remaining, Fix translations (with the right list already chosen), Use/Install the local translator |
| Translation interrupted | The translator stopped (for example Google paused after a block): the translations made so far stay | Use/Install the local translator, Show remaining |
| The game is open | Nothing was touched, or the translations are ready but not written yet | Try again, Reapply |
| Game not supported yet | The reason in plain words (for example Unity IL2CPP without text tables, tables without the source language, a binary catalog or a file checksum that cannot be removed) and that nothing was changed | Create report |
| Official language | The game already has Italian: choose it in its menu | Translate anyway (where possible) |
| Anti-cheat, translator not ready, locked files, wrong folder, nothing to do, already original | The reason and what to do | Install the local translator, Try again, Create report (where useful) |
| Unexpected error | The kind of error; the technical details are in the saved log | Create report |

**Try again** repeats the last operation on the game with the same choices; after installing the local translator, a report or an error in a window command it is not offered (they are unrelated to the game). **Use the local translator** appears if it is already installed and is not the chosen one: it selects it and repeats "Analyze and translate" with the same choices (also after "Translate anyway"); if it is not installed, the button is **Install the local translator**.

If the engine is not supported yet but there are files with text, PatchSmith proposes an **experimental** translation.

### Before starting: the quick checks

Before "Analyze and translate", "Restore originals" and "Reapply" (offered by the end-of-operation dialogs and by the after-play check: it rewrites the saved translations offline, without translating new texts) PatchSmith checks in a moment what you would otherwise find out only after minutes (or hours) of work. For every problem found a dialog explains what is going on, and the operation starts only if you choose to continue:

| What it checks | When | The dialog | Buttons |
|---|---|---|---|
| The game is open | always | Close it and press "Try again" | Try again (checks again), Cancel |
| The From/To languages | Analyze and translate | Language not recognized, or From and To are the same: choose from the list. A code outside the list can be used | OK, or Continue anyway / Cancel |
| The game folder is writable (a test file, removed right away) | always | Windows denies access: with the game in "Program Files" it explains how to move it (Steam: Move install folder) or to reopen PatchSmith as administrator; otherwise "Controlled folder access" or the antivirus. Nothing is touched | OK |
| Free space: at least 500 MB on the game's disk and 100 MB on the one with PatchSmith's data | Analyze and translate, Reapply | With a full disk the operation would stop halfway | Cancel, Continue anyway |
| The chosen online translator is paused after a block | Analyze and translate | Until when, and that the local translator has no blocks | Use the local translator (or Install the local translator), Continue anyway (only the translations already saved), Cancel |
| The local translator is chosen but not installed | Analyze and translate | It is downloaded only once. Without installing it the game is analyzed anyway: if it already has its official translation that is enough, otherwise it stops before changing the game | Install now, Continue anyway, Cancel |
| The same game is already translated in another folder (see below) | Analyze and translate, folder without translations | Which game it is and how many texts it has saved | Yes, reuse them; No, translate from scratch; Cancel |

"Use the local translator" selects it and starts again; "Install" installs it (with the test sentence) and then starts again on its own, if the test sentence works (if not, a dialog says why). While one of these dialogs is open, the after-play check at opening waits: the operation you choose starts anyway.

**The same game in another folder.** If you copy, move or reinstall an already translated game, the new folder has no translations: PatchSmith recognizes the same game (same Unity product, same Steam game, an executable with the same name, or failing that the same folder name) and proposes to **reuse its translations**, in the same language. If the previous folder still exists (a copy), the new profile starts with a copy of the memory (your corrections too) and of the names not to translate: only the missing texts are translated. If it no longer exists (game moved), the old profile moves to the new folder. To recognize a moved game the profile remembers its data (`identity`); profiles from earlier versions are recognized by their folder, if it still exists, or by name.

### Fixing translations

**Fix translations…** opens a window with all the translations of the chosen game: on the left the original text, then the translation and the problem found; for games that reveal how much room their texts have (GameMaker with line breaks) also the "Limit" column: longest line of the translation / characters that fit in the game. For Unity the translation is the one the game really shows (XUnity's cache), including the lines translated while you played.

- **Search** finds a text in the original or in the translation (case-insensitive).
- **Show** picks the list, with how many texts it has:

  | List | What it contains |
  |---|---|
  | Left untranslated | texts without a translation (also those the translator discarded) and translations identical to the English; for Unity cache lines like `Open the door=Open the door` |
  | Too long | texts that overflow their room: in games that reveal it (GameMaker with language files) the longest line among the game's versions, measured as the game writes it; in the others, short labels (up to 3 words) that became at least twice as long as the original; and labels that PatchSmith already asked shorter without success (see "Letters the fonts lack and labels that are too long") |
  | Not written to the game | translations the game could not display (characters its fonts lack, a shape different from the original), with the reason |
  | Using numbers or placeholders | texts with numbers or codes like `{0}`: first those where they do not match |
  | In another language | translations in another language, or copied from an official translation in another language |
  | Suspicious | saved translations that fail the checks, lines translated while you played without all the checks, a word that became a sentence, a button with an infinitive verb ("Esportare" instead of "Esporta") |
  | Fixed by hand | your corrections |

- Choose a line and fix the translation in the box below. While you type PatchSmith shows the length (and how much room there is in the game) and warns if a placeholder is missing or extra, or if numbers or lines differ from the original. Below it also shows the line's full problem.
- **Save and apply to the game** saves the corrections (all together) and closes the window; then PatchSmith writes them into the game on its own, like "Reapply" (checks first: if the game is open, close it and press "Try again"). An empty translation is not saved; with a placeholder that does not match PatchSmith asks whether to save anyway. Closing the window with unsaved corrections asks whether to save them.
- Corrections go into the game's memory with the same lock as the live translator (the file always stays valid); `.automatiche.json` keeps the machine translation (so they are recognized and PatchSmith never touches them again; for a text that was not there, the previous one, or an empty entry if it had stayed untranslated); they leave the list of untranslated texts, and short texts go into the shared memory of all games. For Unity the XUnity cache line is replaced only with the game closed: until it is written it stays in `memory/<game>.<language>.da_scrivere.json`, and it is written by "Try again" (with the game closed), the next "Reapply" or "Analyze and translate" or, if you pressed nothing, by the after-play check the next time PatchSmith opens. If that line was changed by hand in the file in the meantime, it is left as it is.
- **Re-translate selected…** re-translates the chosen lines (Shift or Ctrl to choose more than one) with new requests every time you press it, so the translation can come out different, also for lines fixed by hand. With the local translator you choose the model (Fast or Quality, among those downloaded). If the new translation fails the checks, the previous one stays. Then it writes them into the game.
- **Open the file (advanced)** opens the translations file as before; if Windows has no program for that file type, with Notepad. If the translations file is damaged (for example edited by hand with a mistake), the window does not open and says so, offering to open the file.
- **Keyboard:** Ctrl+F searches; Enter (or F2) from the list goes to the translation; Ctrl+Enter confirms and moves to the next line; Esc undoes the line's edit (from the list it closes the window); Ctrl+S saves; Alt+arrows (or Ctrl+PgDn/PgUp) change page; Ctrl+A selects all the lines of the page. The list shows 300 lines per page, so it stays fast even with tens of thousands of texts.

In the end-of-operation dialogs, with texts left untranslated, translations the game cannot display or labels that are too long, there is the **Fix translations** button, which opens the window already on the right list; it is also in "Show remaining".

### Letters the fonts lack and labels that are too long

Two things that used to show up only while playing, PatchSmith now checks on its own, for every engine.

**Letters the game's fonts cannot draw.** In their place the game would show a little box (or nothing, or with XNA it would crash). PatchSmith does not block the translation: it only replaces those letters with the closest one the font has (`è` becomes `e'`, `ò` becomes `ó` if the font has it, otherwise `o'`; typographic quotes and dashes become plain ones). A translation that cannot be fixed this way (for example with an emoji) stays in the original language, goes into the "Not written to the game" list and, if PatchSmith wrote it, is redone without those characters at the next translation. In the memory the translation stays the real one: the adaptation only applies to that game.

| Engine | How it knows which letters the fonts have |
|---|---|
| GameMaker, XNA/MonoGame | it reads the game's fonts (as before) |
| Unity Localization (text tables) | it reads the game's TextMeshPro fonts, in the bundles and in the `_Data` files. A letter is fine if at least one font can draw it (TextMeshPro looks for the ones missing from the other fonts); a "dynamic" font counts with every letter of its `.ttf` file. If it cannot tell (no TextMeshPro font, dynamic fonts without their `.ttf`, Windows fonts) it limits nothing. The first time on a big game it can take up to a few minutes (SWORN: 8 seconds); then the result stays in `memory/<game>.unity/font.json` until the game's files change |
| Unity with XUnity | while playing: PatchSmith turns on in BepInEx's log (`BepInEx.cfg`, `[Logging.Disk] WriteUnityLog = true`) Unity's messages, where TextMeshPro writes every letter it does not find (*"The character with Unicode value è was not found in the [...] font asset"*). The after-play check reads them (also from `Player.log`) and fixes them (see the table below) |
| Unreal | the letters of the game's official translations in Latin-script languages (French, German, Spanish...): if they use them, the font has all the common accented letters (also `ì` and `ò`). Without official translations with accented letters it limits nothing |
| Web | no limit: the browser uses Windows' fonts |

**Labels that are too long.** After translating, PatchSmith looks at the short labels (up to 3 words: buttons, menu items) it translated automatically. One is too long if it goes beyond the room it usually has:
- if the game has official translations of that text (or, in GameMaker, the width of its lines): more than 1.3 times the original plus 2 characters and more than the longest official translation;
- otherwise: at least twice the original and 7 more characters (`Credits` → `Ringraziamenti`, `Slider` → `Dispositivo di scorrimento`). In the memories of the games already translated these are 3-8% of the labels; the 1.3 rule alone caught 15-30%, even the ordinary `Statistiche` and `Combattimento`.

With the **local translator** labels that are too long are requested again, once, giving the maximum number of characters: the shortest translation that passes the checks is kept (`Credits` → `Crediti`). Those still too long, and with the other translators (which know nothing of a maximum length) all of them, go into the **"Too long"** list of "Fix translations", with the original text, and the end dialog says so. Labels noted with an online translator are requested shorter (once) at the first translation with the local translator; with the local translator, "Re-translate" on an item of the "Too long" list also requests it with its room. Your corrections by hand are never touched.

### After playing: the automatic check

After a session the game leaves traces on disk: the Unity and BepInEx logs, the crash folders, the new-lines translator's journal, files an update put back to the original. **When you open PatchSmith** the program looks (only at the files' dates, in a moment) at which translated games have new traces since the last check and checks them on its own, in the background: the result appears in the window's log. Only if there is something to do, the **Last session details** and **Re-translate N phrases** buttons appear at the bottom of the window (to the left of "Create report"). The check at opening only does the quick things on its own (crashes, configuration, new-lines translator, files put back by the game); it leaves the journal's lines to re-translate to the **Re-translate N phrases** button (or "Re-translate the phrases" in the Details): re-translating them would mean loading the local translator or waiting for Google's pauses with the buttons frozen. If you already started another operation in the meantime, the check starts on its own as soon as it ends (the log says so). From the **Details** dialogs the buttons ("Restore originals", "Reapply", "Create report"...) apply to the game of that dialog: the chosen folder becomes its folder. From the command line: `python -I smith.py check <folder>` (without a folder: all games); with `--dry-run` it says what it would do without touching anything.

Only the traces that arrived **after the last time PatchSmith wrote into the game** count (install, Reapply, a correction): a crash from before the correction no longer counts. Unity's log and crash folders are the same for every copy of a game: a log only counts if it belongs to the right copy (the game's path written inside: `Mono path[0]`, or `Discovering subsystems at path` in IL2CPP games). Of very large logs (over 2 MB) the beginning and the end are read. After a PatchSmith update, Unity games are checked again once at opening, to bring them to the new version's configuration.

| What it finds | What it does on its own |
|---|---|
| Unity: the game closes at startup while BepInEx starts (crash inside `Application:.cctor`, or `LogOutput.log` without "startup complete") | BepInEx starts later, at the first MonoBehaviour, with any Unity version. If the game closes **even after**, with a real crash (a Unity crash folder with BepInEx in the stack, or a crash in the Windows log), it proposes "Restore originals" (it does not do it on its own): the game probably does not accept mods. An incomplete `LogOutput.log` without a crash (for example the game closed while loading, with BepInEx already starting at the first MonoBehaviour as in Unity 6 games) is just for your information |
| Unity: the new-lines translator stopped (XUnity with Google giving errors, "Failed:", the anti-spam check, too many errors in a row) | It puts PatchSmith's translator back in the game. If it already was PatchSmith's, the remaining lines are re-translated from the journal; with no translators available it proposes "Install the local translator" |
| Unity: new lines are requested from Google by XUnity (or by nobody), for example in a game translated with an old version; Google may be paused | It hands them to PatchSmith's translator. If no translator is available right now, it proposes "Install the local translator" |
| Unity: installed with a different version of PatchSmith, XUnity or BepInEx configuration changed (for example an XUnity test option turned on, or Unity's messages not written to BepInEx's log) | It puts back this version's configuration and writes the version in the manifest |
| Unity: PatchSmith's translator cannot start (Python or PatchSmith moved, the plugin writes that something was missing) | It rebuilds the plugin and its command |
| Unity: the translation's install was interrupted halfway | Nothing on its own: it proposes "Restore originals" and then "Analyze and translate" |
| Unity: installed files are missing (for example removed by the antivirus) | It puts them back from the components already downloaded; if they go missing again it proposes "Reapply" and a report |
| Unity (also Unity Localization, from `Player.log`): letters of the translations the game's fonts cannot draw (TextMeshPro warnings) | It notes them in the game's profile (`missing_chars`): from then on every translation written into the game, also by the new-lines translator, replaces them (`è` → `e'`, only with plain letters). It fixes those already written right away: with XUnity the cache lines written by PatchSmith (those changed by hand stay, and it tells you), with Unity Localization with "Reapply". Letters found only in the game's texts, not in the translations, do not count |
| Unity: journal lines left untranslated or translated without all the checks while you played, lines translated but not saved | Lost lines go back into the memory right away. The others stay to do at opening, with the **Re-translate N phrases** button; with that button or from the command line they are re-translated with the chosen translator and put into XUnity's cache (a temporary line is only replaced if it is still the journal's: your corrections stay). The next "Analyze and translate" retries them too. If the chosen translator is the local one and it is not installed, the dialog proposes "Install the local translator" |
| Unity: corrections made in "Fix translations" with the game open and not written yet (you did not press "Try again") | It writes them into XUnity's cache, without reinstalling anything. A line changed by hand in the file after the correction stays as it is |
| Temporary files left in the memory by an interrupted save | It removes them |
| Games with translated files: the game put back its original files (update, Steam file verification), or removed the added language; Unity Localization: an update put the checksum check back in the bundles' catalog | "Reapply" on its own (for GameMaker it also puts the added language back in the options: the game had changed it) |
| Games with translated files, Unreal, XNA: the game was updated with new texts | It proposes "Analyze and translate" |
| GameMaker: another language is chosen in the game's options | It just tells you: it may be your choice |
| Unreal: the game's log says the translation pak was not loaded, or that another language is chosen | It tells you, with the report or the language to choose |
| Other engines: the game closed suddenly (Windows Application log, events 1000 and 1026) | It proposes "Restore originals", which asks for confirmation |

The rules of the automatic fixes:
- **only with the game closed**: if the game is open nothing is touched and nothing is written, not even the result; it retries at the next check;
- **only once per trace**: every attempt stays in `profiles/<game>.controllo.json` with the date and how it went; if the problem comes back, it stays "to do" with the right button and the report;
- **reversible**: they only touch PatchSmith's files in the game (BepInEx and XUnity configuration, plugin, translations cache) and the memory, or put back the saved translations; "Restore originals" removes everything as always;
- **never the game's saves**;
- everything ends up in the log (`logs/...-controllo.log` for the check at opening) and in the **Details** dialog.

If PatchSmith is updated while its window is open, at the next operation a warning says to close and reopen it (otherwise the old code would still be used).

**Logs and report.** The log of every operation is also saved on disk, in `logs/` (one file per operation, `YYYYMMDD-HHMMSS-<game>.log`; the last 30 are kept), with the technical details of unexpected errors that the window does not show. **Create report** puts in a zip on the Desktop:
- the logs of the last 5 operations on the game and the latest of each general log (window errors, local translator install, check at opening), the profile, the settings **without the DeepL key and without the email**, the translators' state;
- the engine detection, PatchSmith's manifests in the game, the list of the game's files that were changed and PatchSmith's version;
- the list of texts left untranslated with the reason (`memory/<game>.<language>.problemi.json`), the corrections not yet written into the game (Unity: `<game>.<language>.da_scrivere.json`) and the letters the game's fonts can draw (Unity Localization: `font.json`);
- the result of the after-play check (`profiles/<game>.controllo.json`: traces seen, fixes tried and how they went);
- for Unity: `BepInEx/LogOutput.log`, the XUnity and BepInEx configuration, the translator's startup state (`BepInEx/patchsmith-servizio-stato.txt`), the end of the live translator's log and journal, `Player.log`, `Player-prev.log` and the `Player.log` of the last crash;
- for Unreal: the game's log (`%LOCALAPPDATA%\<project>\Saved\Logs\<project>.log`), from which the after-play check tells whether the translation pak was loaded and which language is chosen.

No game files and no saves. If the key or the email had ended up in a log (even inside a web address), in the report they become `<removed>`; your Windows user folder becomes `%USERPROFILE%`. If the Desktop is not writable (for example with Windows' "Controlled folder access"), the report goes in the `logs/` folder and the dialog says where.

## From the command line

```bash
python -I smith.py auto "D:\Games\MyGame"
```

This command does everything automatically: it detects the engine, finds the texts, translates and applies. The Italian names of commands and options from before (`traduci`, `--forza`...) still work.

| Command | What it does |
|---|---|
| `python -I smith.py detect <folder>` | Recognizes the engine and the suggested route |
| `python -I smith.py scan <folder> --name my-game` | Creates the profile to check by hand |
| `python -I smith.py translate my-game` | Translates the missing texts and applies them |
| `python -I smith.py apply my-game` | Reapplies from the memory, offline |
| `python -I smith.py restore my-game` | Puts back the originals; also accepts the game's folder, and also restores the language set by PatchSmith |
| `python -I smith.py status my-game` | How many texts are translated, with examples |
| `python -I smith.py package` | Builds the release package (see "Release package") |
| `python -I smith.py check <folder>` | After-play check (see above); without a folder all games, `--dry-run` fixes nothing. Exits with 0 if nothing is left to do, 2 if something is left (also a fix postponed because the game is open, or not done with `--dry-run`), 1 for an error |

Useful options:
- `--from` and `--to`: the languages. `--from` defaults to `auto` (language detected from the texts), `--to` to the Windows language. They accept codes (`en`) or Italian names (`inglese`);
- `--backend locale` / `deepl` / `mymemory` / `google`: the translator to use. If you do not give it: the local one; if it is not installed, the error gives the command to install it. The DeepL key, the MyMemory email and the local model are read from `settings.json` (the interface saves them) or from the variables `PATCHSMITH_DEEPL` / `PATCHSMITH_EMAIL` / `PATCHSMITH_MODEL` (`4b` or `12b`);
- `python -I smith.py local`: state of the local translator and, if it is not installed, the model suggested for your graphics card; `--install 12b` (or `4b`, or both) downloads and installs it, then translates a test sentence (exits with 2 if the test fails);
- `python -I smith.py services`: state of the translators (pauses after a block); `--reset google` turns it back on (for a disabled translator the list says so too);
- `--backend fake`: technical test offline, prefixes `[it]` to the texts. Only available from the command line, not in the graphical interface;
- `--force` (for `auto`): also tries with engines that are not supported;
- `--regenerate`: recreates the profile from scratch.

Messages talk about the interface's buttons; when there is a command to fix something (for example installing the local translator), the command line writes it under the error. The exit codes stay the usual ones: 0 done (also when the game already has the official language), 2 partly done, nothing to do or not possible, 1 error.

**Where your data lives.** Profiles (`profiles/`), translation memories (`memory/`, with the machine translations, the texts with problems, the origin of the shared memory, the corrections to write into XUnity's cache), settings (`settings.json`), translators' state (`services.json`), languages that were set (`languages/`, `configurations/`), copies of saves (`save-backups/`), logs of the operations done from the interface (`logs/`) and the result of the after-play check (`profiles/<game>.controllo.json`) live next to the program. With the environment variable `PATCHSMITH_DATA` they go into another folder; the program and the downloaded components (`components/`) stay where they are. The automatic tests use it so they never touch the real data. The Unity games' live translator is started by the game, which normally does not have the variable: it uses the data next to the program. Development versions before 0.1.0 used Italian names (`profili`, `memoria`, `registri`, `salvataggi`, `configurazioni`, `lingue`, `componenti`, `impostazioni.json`, `stato_servizi.json`): at startup PatchSmith moves them to the new names on its own, without copying or deleting anything (a folder in use stays where it is and is used until it can be moved), and a game translated earlier that asks for the profile with the old path finds it.

## How it works

1. **Detection.** It uses SteamDB's rules on file names (about 125 engines, MIT license), checks the first bytes of files (Godot `.pck`, GameMaker `data.win`, KiriKiri `.xp3`, Ren'Py `.rpa`, `UnityFS`...) and recognizes web frameworks inside Electron/NW.js. Every engine gets a score with the evidence. It also reports anti-cheats.
2. **Texts in the data (`web-json` adapter).** It looks for JSON files with readable text and chooses which fields to translate. It automatically excludes:
   - technical keys such as `id`, `url`, `path`, `author`, `...Id`, `...Type`;
   - values that are not text: file names, colors, identifiers such as `room_12` or `UnlockFirstNewRule`, lines of code, values of 1–2 characters;
   - the **project data of Construct** (`data.json` of the export, `{"project": [...]}`): its strings are the names of layouts, objects, animations, sounds and variables that the game looks up, and translated the game no longer finds them (empty buttons). It is never translated; if an older version translated it, "Analyze and translate" puts the original back. The game's own text tables next to it (e.g. a `Database.json` with one field per language) are translated as usual.

   Also:
   - **JSON with comments** (`//` and `/* */`) and with a comma before `}` or `]`: many games accept them, and PatchSmith reads them. It rewrites them changing only the translated sentences: comments, spaces, line breaks and numbers stay identical byte for byte;
   - **language files** `{key: text}`, also split into sections (`"menu": {"_play": "Play"}`): such a file, or a file present in several languages (`lang/en.json`, `lang/fr.json`, `stringsEN.json`, `stringsFR.json`), is translated entirely, **single words too** (`wood`, `PLAY`, `quit`). Identifiers stay out (`icons/wood.png`, `menu_title_2`). Files with a list of objects are not language files (`[{"id": "sword", "name": "Sword"}]`: that is game data, like the sounds' `PluginInfo.json`, where only sentences are translated with the usual rules), nor, for files without the language in their name, saves and options files;
   - **CSV tables with one column per language** (`translations.csv` with `id,english,french,...`): the column of the original language is translated (see "GameMaker games"). If the Italian column is already filled, it is the official translation;
   - in **GameMaker** and **XNA/MonoGame** games that end up here (no recognized language file: experimental translation), the profile remembers the engine and the translations adapt to the characters the game's fonts can draw, as in the other cases;
   - in the log: the skipped files, with how many there are (invalid or too large `.json`, files with few texts left out, web game files too large for the menus).
3. **Web games' menus (`web-ui` adapter).** Menu texts are inside the JavaScript code. **The game's code is not changed.** Instead the tool:
   - extracts the strings with a small JavaScript reader;
   - translates them;
   - generates `patchsmith-traduzione.js` (the dictionary plus the injector) and adds a `<script>` line to `index.html`.

   While you play, the injector only replaces **displayed** texts that exactly match a dictionary entry: on-screen texts, tooltips, placeholders, the window title, text drawn on a canvas. A string extracted by mistake does no harm, because it never appears on screen.

   **One-word buttons** (`PLAY`, `OK`, `quit`) also enter the dictionary, when they are surely visible: the text and the visible attributes of the HTML and, in the JavaScript, the strings given to `textContent`, `innerText`, `title`…, drawn with `fillText` or `add.text`, or written as `label:` or `text:`. Code, CSS, camelCase names or names with an underscore, keys and fonts stay out.
4. **The game's language.** With "From" on automatic it is recognized from the extracted texts:
   - first from the script: kana → Japanese, hangul → Korean, ideograms → Chinese, then Cyrillic, Greek, Arabic and others;
   - for the Latin script, from the most frequent words (English, Italian, French, German, Spanish, Portuguese, Dutch, Polish, Turkish, Swedish).

   If the game is already in the target language, the program does not translate it.
5. **Translation.** It uses the chosen translator (the default is the local translator, on your PC; see "Translators"):
   - with online translators the requests go in blocks (with Google of about 4,500 characters), with pauses and retries;
   - line breaks are kept;
   - placeholders and codes (`{name}`, `%d`, `<b>`, `\C[3]`, `**`, color and font tags such as `[yellow1]…[/c]`) are protected.
6. **Translation memory.** The file `memory/<game>.it.json` contains `{"original": "translation"}`. It can be edited by hand, and sentences already translated are not sent again. Next to it there are:
   - `<game>.it.automatiche.json`: the translations as PatchSmith wrote them. A memory entry different from this one is your correction, and PatchSmith never touches it; an entry missing here (translated by a version that did not keep this file) counts as automatic;
   - `<game>.it.problemi.json`: the texts with a problem, with the reason (see "Discarded texts and translations to redo"), also labels too long for their room;
   - `<game>.it.da_scrivere.json` (Unity only): the XUnity cache lines to change with the corrections made in "Fix translations", until the game is closed;
   - `<game>.unity/font.json` (Unity Localization): the letters the game's fonts can draw, until the game's files change. The letters the game said it lacks are kept in the profile instead (`"missing_chars"`): removing them from there, PatchSmith writes them again.

   To fix a translation use "Fix translations" (see "Fixing translations"): the window saves the memory without ever damaging it and writes the corrections into the game.
   - **The game's names:** the studio and the title stay as they are ("Cup Dog Games" does not become "Giochi di cani da coppa"). PatchSmith reads them, without changing anything, from Unity games' `app.info`, from the executables' version information (Properties > Details: company and product) and from the game's name on Steam; it writes them in the profile (`"do_not_translate"`, where you can add more by hand, or remove one if for example you want the title translated: a removed name does not come back, because the ones already found stay in `"found_names"`) and protects them like the game's codes. A one-word name that also appears in lowercase in the texts (for example "Control") is an ordinary word, and is translated.
7. **Safe writing.**
   - The original is saved next to it as `*.patchsmith-originale`. The copy is verified first and then renamed, so a half-written backup can never remain.
   - Next to the backup there is `*.patchsmith-info`, with the original's checksum, the checksums of everything the program wrote and the files it created. It lives inside the game's folder, so it works for any profile and even if you move the tool.
   - The checksum of what it is about to write is recorded **before** writing.
   - If a backup turns out to be altered, the program stops and touches nothing.
   - Before writing a JSON it checks that the structure is **identical**: only the chosen strings change, and no `NaN` or `Infinity`. A JSON with comments is rewritten replacing only the translated sentences, and read back to check; the same for CSV tables.
   - The generated script is checked with Node.js, if it is installed.
8. **Game updates.** If a file was changed by someone else (an update), the new file becomes the original, the previous one is kept with a version number and only the new sentences are translated. Restoring never overwrites a file updated by the game.
9. **Restore.** It brings back to the original **all** the changed files in the game's folder, even without a profile. Then it deletes backups, information and scripts.
10. **Tests.** The test translator (`fake`) uses a separate memory (`*.prova.json`), so it does not spoil the real one.

The program recognizes the same game even if you choose a parent or child folder, and reuses the same profile.

**Japanese, Chinese and Korean.** Texts in these languages have their own rules:
- entries of 1–2 characters are allowed (設定, はい, 시작);
- long sentences without spaces and texts with digits are allowed;
- file paths (`img/背景`), file names and code stay excluded.

The menu injector also recognizes entries of 1–2 characters.

## Unity games

**Games with Unity Localization (also IL2CPP).** Many recent Unity games keep each language's texts in tables inside Addressables bundles, for example `StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle`. For IL2CPP games, where mods cannot be used, PatchSmith:
- reads the bundles (UnityFS, LZ4 or LZMA compression) and the serialized files inside them, following the type tree;
- translates the texts of the English table (each entry's `m_Localized` field), protecting codes such as `{action.HeavyAttack}` and the `<b>`, `<color>` tags;
- rewrites the bundle without compression, which Unity reads all the same. It only changes those texts, and reads it back to check that everything matches;
- keeps the verified backup of the original and puts it back with "Restore originals".

Each bundle's language is recognized even when the name lacks the code in parentheses: from the content (the language code written in the tables, for example `localization-string-tables-english_assets_all.bundle`, `localization_en_assets_all.bundle` or `chinese(simplified)`) and, if the bundle cannot be read, from the language's name. The asset tables (images and sounds) are not touched. If the source language has several bundles, they are all translated. Without tables in the source language PatchSmith says so, with the languages the game has: choosing one of them in "From:" and retrying translates that one.

**Checksum (CRC).** Some games write in their catalog (`StreamingAssets/aa/catalog.json`) the checksum of each bundle and refuse a changed bundle. Then PatchSmith, before rewriting the bundle, sets to 0 the checksum **of that bundle only** in the catalog: the file keeps the same length and the rest is identical. The catalog has its backup like the other files: "Restore originals" puts it back, and if the game puts it back on its own (update, Steam file verification) the after-play check reapplies the translation. PatchSmith changes nothing and explains why when:
- the catalog is binary (`catalog.bin`, in the most recent Unity games) and the bundle's record cannot be found with certainty. Usually it is found: each bundle has a record with checksum, name, CRC and size, and only the CRC's 4 bytes are zeroed;
- the catalog checks the checksum but does not name the bundle to translate.

First of all it checks for an anti-cheat, for these games too: with an anti-cheat it changes no file.

In the game English stays selected. If the game already has the target language among its tables (also recognized from the content), PatchSmith says to choose it in the menu, for Mono games too.

**Fonts.** Before rewriting a table PatchSmith looks at which letters the game's TextMeshPro fonts can draw (see "Letters the fonts lack and labels that are too long"): a letter that no font has becomes the closest one (`è` → `e'`) only in the game's table. "Dynamic" fonts draw every letter of their `.ttf` file. On SWORN, Megabonk, Rogue Blight, LumenTale, LEGO Party, MOUSE P.I. and Vampire Crawlers the fonts have every Italian letter: nothing changes. If after a session `Player.log` says a letter is missing, the after-play check notes it and rewrites the tables without it.

**Unity Mono games.**

For Unity (Mono) games PatchSmith **does not change the game's files**. It downloads, only once and from the official releases on GitHub, BepInEx 5 and XUnity.AutoTranslator. The versions are pinned and the SHA-256 checksum is verified; the files stay in `components/` and serve every game.

Then it installs them in the chosen game's folder: `BepInEx/`, `winhttp.dll`, `doorstop_config.ini`, `.doorstop_version`.

- **Pre-translation:** before you play, PatchSmith reads the texts directly from the game's files (read-only), translates them in blocks and puts them into XUnity's cache. So lines appear **translated from the very first time**. It reads, in this order:
  - the Unity Localization text tables in the game's language, if there are any (not those of the other languages);
  - the files in `<game>_Data`, in pieces (even those of several GB, without filling the memory);
  - the game's code: `Assembly-CSharp` and also the game's other modules (for example `SunHaven.Core.dll`, `Mouse.dll`, and older games' `Assembly-UnityScript`), not the engine's, .NET's or third-party libraries;
  - the compressed files: `data.unity3d` and the bundles in `StreamingAssets`, without images and sounds, first those that usually have texts. At most 400 MB and one minute: the log says how many it read, and the texts left out are translated while you play.
  - Automatic filters: no object names (`Paper (17)`), internal names (`Level_Grass_0`) or texts in other languages already in the game.
  - At most 20,000 texts (600,000 characters): the others are translated while you play.
- **IMGUI interfaces:** XUnity usually does not translate texts drawn with IMGUI (`OnGUI`). If the game's code uses them, in `Assembly-CSharp` or in another of its modules (not in third-party libraries), PatchSmith turns their translation on. For games already installed the after-play check does it, putting the configuration back in order.
  - The names of languages in the menus (English, Español, 日本語…) stay unchanged.
  - Translations already in the cache, including your corrections, are not overwritten. The exception are the wrong lines **written by PatchSmith**: at every "Analyze and translate" (and "Reapply") PatchSmith checks the cache and flags the lines left identical to the original (with at least two frequent words: "Xbox" or "Codec" are names and stay), in another language, with more or fewer lines than the original, with a lost `{{A}}` or with a translated game name; plus the live translator's temporary answers (in the journal: `relaxed`, `rejected`, `deferred`). It redoes only those whose translation is exactly the one from the memory, the machine translations or the journal, and replaces them only with a new translation that does not have the same problem. A line changed by hand (or written by others, for example by XUnity with Google before PatchSmith) stays as it is, even if it looks wrong.
- **Live translation:** lines not found in the files, for example those composed on the fly such as "Previous save was 3 mins ago", are translated while you play. The first time they appear in the original for a moment, then translated. XUnity's batching is turned off because with Google it made mistakes and doubled the wait. In the game: **ALT+T** toggles original and translation, **ALT+R** reloads the translations after a correction.
- **New lines are always translated by PatchSmith**, whatever translator you chose: XUnity sends them to PatchSmith's translator on your PC, which uses the game's memory and the shared one, checks the translations and respects the online translators' pauses.
  - Examples: lines with numbers in games with an IMGUI interface, such as "Hold RMB 0.65s to leap up to 25 m". XUnity removes the numbers (`{{A}}`) and puts them back later; PatchSmith protects them together with the attached unit, such as `{{A}}s`.
  - **Translators in a row:** first the local translator, if it is installed (with the Fast model, if present: the graphics card also serves the game); then the online translator you chose (DeepL with your key, MyMemory); then free Google, if it is not paused. If one does not answer or is paused it moves to the next. Google goes through the same cautious limits as the translation: if it would have to wait more than a minute, the line stays in the original for now. After a network error (or if DeepL asks to wait) it retries once after a few seconds, then moves to the next translator: XUnity does not wait minutes for an answer.
  - **Never an error for a hard line:** XUnity stops asking for translations after 5 errors in a row. If every translator discards a line (for example because the translation loses a `{{A}}` or is too long), PatchSmith retries once the pieces between the numbers and answers with the best translation, as long as it has all the `{{A}}` and is in Italian; otherwise it answers with the original. These fallback answers do not go into the memory. The error reaches XUnity only if no translator is reachable; while the game is closing the translator no longer translates anything.
  - **Lines journal:** every line requested while you play ends up in `memory/<game>.richieste.jsonl`, one row per line with text, outcome (`ok`, `relaxed` = fallback answer, `rejected` = left in the original, `deferred` = postponed, `error`, `interrupted` = interrupted), translator and reason. Lines that are not `ok` are those to re-translate with the game closed. Beyond 2 MB the journal becomes `.richieste.jsonl.1`.
  - How it works: a small plugin (`BepInEx/plugins/Patchsmith/Patchsmith.Servizio.dll`) starts, when the game opens and without windows, `smith.py xunity-service` (games set up by earlier versions call it by its Italian alias, `servizio-xunity`, which still works).
    - The service only answers on `127.0.0.1`, on a fixed port for each game, and is connected to XUnity with `Endpoint=CustomTranslate`, without XUnity's anti-spam check (which would turn the translation off when many new lines appear together) and with texts up to 2500 characters.
    - The plugin is compiled with Windows' `csc` using the game's own libraries; the source is `components/plugin/PatchsmithServizio.cs`.
    - The command is written in `BepInEx/config/patchsmith-servizio.cfg`. If the Python it names no longer exists (moved or updated), the plugin tries the Windows Python launcher (`py -3`). How the startup went is written in `BepInEx/patchsmith-servizio-stato.txt`.
  - It starts Ollama and loads the model right away, so the first line does not wait.
  - **It closes on its own** when you close the game and frees the graphics card. It stops receiving lines at once (so, if you reopen the game, the new translator starts); a line being translated finishes and is saved before closing.
  - Log: `components/ollama/servizio-xunity.log`.
  - The choice stays in the game's profile (`"live": "service"`): "Reapply" also puts PatchSmith's translator back. Writing `"live": "google"` by hand, new lines are requested from Google by XUnity on its own.
  - If the plugin cannot be prepared (for example Windows' C# compiler is missing), new lines are requested from Google by XUnity on its own, but **never while Google is paused**: then they stay in the original and the final dialog says so.
- **Shared memory:** the game's memory, the machine translations and the shared memory are written both by the interface and by the translator started by the game. Whoever saves takes a lock (`<file>.lock`), reads the file again and adds only its own changes: nobody deletes the other's lines. If the `.lock` cannot be opened for a moment (another program is removing it), it retries instead of saving without the lock. The same rules hold for the texts with problems (`problemi.json`) and for the shared memory's origin. A file damaged by hand is kept as `<file>.illeggibile` before being rewritten; the `.tmp` files left by an interrupted save are removed by the translator at startup.
- **Saved translations:** `BepInEx/Translation/<language>/Text/_AutoGeneratedTranslations.txt` contains `original=translation` lines. "Fix translations" shows these lines together with the game's memory and, when saving, replaces the corrected ones (with the game closed); "Open the file (advanced)" opens this file.
- **Letters the fonts lack:** PatchSmith turns on in `BepInEx.cfg` the writing of Unity's messages to BepInEx's log (`[Logging.Disk] WriteUnityLog = true`; in games already installed the after-play check does it). There TextMeshPro writes every letter the game's fonts lack: after the session PatchSmith notes it in the profile and replaces it in the cache lines it wrote, and from then on the pre-translation and the new-lines translator avoid it too (the memory keeps the real translation).
- **After-play check:** PatchSmith does it on its own at opening (see "After playing"). `python -I smith.py status <game>` reads `BepInEx/LogOutput.log` and says whether BepInEx and XUnity started (only with "Chainloader startup complete": "Chainloader started" is there even when the game closes right after) and how many lines were translated.
- **Version:** the `patchsmith-unity.json` manifest remembers which PatchSmith version wrote the configuration (`patchsmith_version`, also in the Unreal and XNA manifests and in the translated files' information); the after-play check updates games installed with a different version. XUnity's test options (`EnableTextPathLogging`, `EnableTranslationHelper`, `CopyToClipboard`) always stay off.
- **Restore:**
  - it only removes the files put there by PatchSmith and those created by BepInEx/XUnity;
  - mods you added stay where they are;
  - before removing everything it saves the translations in the memory (`memory/<game>.unity/`), which come back with "Analyze and translate".
- **Safety:**
  - it does not install if the game is open, if there is already another mod loader or if there is an anti-cheat;
  - everything it adds is listed, with checksums, in `patchsmith-unity.json`;
  - if the install is interrupted, it is rolled back.

## Unreal Engine 4 games

For Unreal games (UE4, classic `.pak` archives) PatchSmith **does not change the game's files** and downloads nothing.

- **Texts:** it reads the game's `.pak` files read-only and finds the `.locres` localization files of the original language. The language comes from the `.locmeta` file (even if it is in another pak), for example `DDS/Content/Localization/Game/en/Game.locres`. Small paks "mounted" in a deeper folder also count (in Remnant `pakchunk1` starts from `Remnant/Content/`): the patch pak always uses full paths.
- **Translation:** it translates the sentences with the chosen translator, protecting:
  - placeholders such as `{count}`;
  - style tags such as `<Bold>…</>`;
  - separators such as `;-;`.
- **Fonts:** the letters the game's official translations in Latin-script languages use, the font can draw; if they use accented ones, the font has all the common accented letters (also `ì` and `ò`). A character that no language of the game uses becomes the closest one (`—` → `-`); if that is not possible (for example `★`) the sentence stays in the original, goes into the "Not written to the game" list and at the next translation PatchSmith redoes it without it. Without official translations with accented letters there is no limit. On Dungeon Defenders: Awakened (French, German, Spanish, Portuguese) nothing changes.
- **Patch pak:** it writes a new pak, `Content/Paks/zz_Patchsmith_<language>_999_P.pak`, with the translated `.locres` files **at the same path** as the originals. The `_P` suffix and the high number make Unreal load it with a higher priority than all the others, official patches included. So the game shows the translation instead of the English.
  - **In the game the original language must stay selected**, usually English.
- **Checks:**
  - the translated `.locres` is read back and must have keys and header identical to the original;
  - the written pak is read back and must contain exactly the expected files.
  - The pak is first written with a temporary name and put in place only at the end, so an interruption is always undone with "Restore originals".
- **Restore:** it only removes the added pak, if its checksum matches the one listed in `patchsmith-unreal.json`. The original paks were never touched.
- **Game without `.locres`:** the `.locres` files are looked for before creating the profile. If there are none, PatchSmith does not leave an empty profile and explains that the game's texts are inside the other data, which it cannot translate today; if the `.locres` files exist but outside the archives, or if some archives cannot be read, it says instead that they are in files it cannot translate yet. A pak that cannot be read (newer format, encrypted or damaged) is skipped, with a line in the log, and the others are searched.
- **Updates:** `status` reports whether the game was updated. In that case press "Analyze and translate": only the new sentences are translated.
- **Safety:** it does not install if the game is open, if there is an anti-cheat or if the paks are signed (`.sig`), because an added pak would be refused.
- **Limits:**
  - unencrypted pak versions 3–9 (Unreal 4.x), with uncompressed or zlib files;
  - Unreal 5 / IoStore (`.utoc`/`.ucas`) and encrypted paks are not handled yet;
  - text drawn into textures and texts written directly into the assets (not localized by the developers) stay in the original.

## XNA / FNA / MonoGame games (.NET)

Many .NET games keep their texts in **.NET resources**. The original version is inside the executable (for example `RogueCastle.Resources.LocStrings.resources` in `RogueLegacy.exe`). Next to it there is a "satellite assembly" for each language: `de/RogueLegacy.resources.dll`, `fr/...` and so on. When the game asks for a language, .NET looks for its satellite first.

PatchSmith **does not change the game's files** and downloads nothing:
1. **Reading:** it reads the resources from the executable read-only. The .NET reader and the XNB font reader are written in Python.
2. **Translation:** it translates the texts protecting placeholders and codes such as `{0}`, `[x]`, `[Input:12]`, `[Button:LeftStick]` and `\n`.
3. **Fonts:** in XNA a character missing from the font **makes the game crash**. PatchSmith reads the game's fonts (`Content/**/*.xnb`) and only uses the characters that every text font can draw.
   - If the accented letters are missing, the accent becomes an apostrophe: `è` becomes `e'`, `perché` becomes `perche'`.
   - "Typographic" quotes and dots become plain ones.
   - Sentences impossible to adapt stay in the original.
4. **Satellites:** it creates **new** satellites, for example `en/RogueLegacy.resources.dll` and `it/RogueLegacy.resources.dll`. It compiles them with the C# compiler that every Windows has, the .NET Framework one, and verifies them by reading them back.
   - The original language's satellite (`en`) serves games that choose the language from the menu.
   - The target language's one (`it`) serves games that follow the Windows language.
5. **Restore:** it only removes the satellites **created by PatchSmith**, which it recognizes by an internal marker. It works even if the `patchsmith-xna.json` manifest is lost, and the game's files are never touched.
   - If the game already has the target language (an official translation), PatchSmith does not translate and says to choose it in the game's menu.
   - If an `.xnb` font cannot be read (for example it is compressed), to be safe it only uses ASCII characters.
6. **Updates:** `status` reports whether the game was updated.

**In the game the original language must be selected**, usually English.

**Limits:**
- it needs a game with localized .NET resources (with at least one satellite). Without them, PatchSmith looks for the game's text files (JSON, CSV tables) and proposes the experimental translation: there too translations adapt to the game's XNB fonts, because a missing character would make it close;
- texts written directly in the code and texts in the game's own data formats stay in the original;
- strong-name signed assemblies are not handled yet.

## GameMaker games

Many GameMaker games read their texts from **localization files** next to the executable, one per language. For example, Dokimon has `Localization/english_ui.txt` and `Localization/french/ui.txt`.

PatchSmith:
1. **Finds the files** of the original language. It recognizes the language from the path (`english_`, `french/`, `en/`…). The other languages' files are not touched.
2. **Reads the "lines with id" format** and recognizes on its own the two kinds of file:
   - **line based:** `0 Fight`, `5  do next?`. The space after the number is part of the text: it is there to attach it to a name;
   - **block based:** a line with just the number, then one line per dialog box, closed by a line with `.`. An entry can have several sections, for example before and after a fight.

   `//` comments, numbers, empty lines, `.` lines and line breaks (CRLF or LF) stay **identical byte for byte**. Anomalous lines in the original files are not touched.
3. **Translation:** it translates protecting codes such as `/name/` (the player's name), `/n/` (line break) and `/region/`. Multi-line dialogs keep the same number of boxes.
4. **Fonts:** in GameMaker a character the font lacks **shows as a little box** (▯). PatchSmith reads the fonts inside `data.win`, read-only, and adapts the characters. For Italian: if the font has the acute accent but not the grave one, `ì` becomes `í`, `ò` becomes `ó` and `ù` becomes `ú` (so, può, più → cosí, puó, piú); otherwise they become apostrophes. Fonts used only for other languages do not count, that is those with mostly another script (a Korean font, or a Japanese one with a few letters such as `ç ê ñ`: more than 10 times the characters of the other script compared to accented letters), nor libraries' fallback fonts (`scribble_fallback_font`, ASCII only), nor those without any of the accented letters of the game's official translations (for example the chat font). A font with a few accented letters does count instead: it may be the very font of the English texts (in Another Door one font has only the `Á ç é ô` the English file uses), so there `è` becomes `e'`. If a font cannot be read, PatchSmith only uses ASCII characters.
   - **Checks on each translation**, according to how these games read the files:
     - an extra `/` would be taken for the start of a code and would delete the rest of the line, so it becomes `-` (Attiva/disattiva → Attiva-disattiva);
     - the codes must be the same as in the original, and a code ending the line is closed with a space;
     - a dialog line cannot start with a number different from the original, because the game would mistake it for another entry;
     - a line cannot become empty nor turn into the closing `.`.

     A translation that fails the checks stays in the original, without blocking the others.
5. **Safe writing:** as for web games, the original goes next to it as `*.patchsmith-originale` with the checksums. After writing, the file is read back and the structure must be identical. "Restore originals" puts everything back byte for byte.

**In the game the original language stays selected**, usually English, except when the translation becomes a new language (see below). If the game already has the target language, PatchSmith does not translate and says to choose it in the menu.

If you edit an already translated file by hand, PatchSmith notices: it does not mistake it for a game update, so the English original is never lost. To fix a translation use "Fix translations".

**JSON localization files.** Some games, for example Forager, use one dictionary per language (`local/english.json`, `local/french.json`…). Others have the language code attached to the name (Another Door: `localization/stringsEN.json`, `stringsIT.json`, `stringsJP.json`, `stringsBR.json`), texts split into sections (`"menu": {"_play": "Play"}`), `//` comments and a comma after the last entry: PatchSmith recognizes them all, and rewrites the file changing only the translated sentences (comments and layout stay identical byte for byte). PatchSmith:
- **Only translates the original language's file.** The others are the game's official translations and stay intact. A language file that cannot be read is written in the log.
- **Every value is an interface text,** even a single word ("wood"). Only the identifiers left in place of a missing text stay out (`Structure.NuclearFurnace`).
- **Recognizes the game's conventions** by comparing the official translations, and respects them:
  - **line breaks written with a sign** (in Forager `*`: `iron*ore`): the whole sentence is translated, then the line breaks are put back at balanced points between the words, in the same number or fewer (`minerale di*ferro`);
  - **paragraphs** (`**`) and **a symbol at the start of a paragraph** (in Forager `U `): they stay in place;
  - **sentences split by a paragraph** (`U items discarded from your**inventory are instantly**sold instead`): a piece without a symbol, starting in lowercase after a piece without final punctuation, continues the sentence (if the previous piece is a list item or has at least three words: a title such as `Controls**move with WASD` stays on its own). The whole sentence is translated and then put back on the same lines. In Forager there are 42 such cases, and the official translations merge almost all of them in the same way;
  - **placeholders** such as `{game mode}`: protected.
- **Fonts:** to decide which characters can be used, it looks at the fonts the official translations really use. ASCII-only ones, such as the chat or the numbers, do not count.
- **A new language instead of replacing the original.** If the game loads on its own every file in the languages folder (PatchSmith looks for the mask, e.g. `local/*.json`, among the game's strings or in the executable), the translation becomes one more file, `local/italiano.json`, and English stays intact. It really matters: in Forager, with the English language, the main menu's buttons are images with the English text already drawn, and the translated text only shows with another language.
  - If the game has already saved its options (Forager: `%LOCALAPPDATA%\Forager\config.txt`), PatchSmith sets the new language there, so the game starts already translated. It only does so with the game closed, only if the language appears in a single place, with a backup copy in `save-backups/` and touching only that value. Otherwise in the game you choose "italiano" in Options > Language.
  - "Reapply" also sets the added language. If in the meantime you chose another language in the game, PatchSmith leaves it.
  - "Restore originals" puts the previous language back in the options, then removes the added language. It only does so if PatchSmith set it for that folder: the options live in `%LOCALAPPDATA%` under the game's name, so a copy of the game shares them with the real one.
  - A file with the same name that does not belong to PatchSmith, for example a fan translation or an official `italiano.json` arrived with an update, is never overwritten or deleted.
  - An added language fixed by hand, before being rewritten or removed, is set aside as `<file>.patchsmith-modificato`, which the game does not load. To fix a translation it is still better to use "Fix translations".
- **Games written in lowercase, with uppercase letters as icons.** Forager draws text with an **image font** (one letter per frame of a sprite). Lowercase letters look like block capitals, and the font's few uppercase letters are icons: `H` the heart, `EP` the yellow "XP", `U` the bullet, `C` the coin. PatchSmith:
  - finds the image font's character map (in the game or in the executable, when it is YYC-compiled);
  - recognizes the style from the texts: almost no word with mixed case;
  - protects the original's icons (`EP`, `H`) like placeholders;
  - writes the translation all in lowercase, except icons and placeholders (before, `mostra*FPS:` showed "MOSTRA / P" with a yellow P);
  - adapts the characters the font lacks: `ì` becomes `í`, `ò` becomes `ó`, `"` becomes `'`, `;` becomes `,`.

  Texts with normal capitalization, for example system messages, stay as the translator writes them and use the characters of the game's normal fonts: the game draws them with those, not with the image font.
- **Room for lines.** For each text PatchSmith looks at the longest line and the number of lines of the original and of the official translations using the same font. If a translated line goes beyond that room, the line breaks are redistributed between the words, adding lines only as far as the game's versions have them (`vsync:` becomes `sincronizzazione*verticale:`, like the French `synchro*verticale :`).
  - A short label that does not fit stays as it is if at least two official translations leave it identical to the original (`vsync:`, `extras`).
  - One-line labels longer than all the game's versions, with the local translator, are requested shorter once, with that number of characters (see "Letters the fonts lack and labels that are too long").
  - Texts that stay too long are listed in the log (with the English text), to be shortened with "Fix translations": they are in the "Too long" list, and while you type the window says how much room there is.

**CSV tables with one column per language.** Some games keep all their texts in a table (Megaloot: `translations.csv`, with `id,english,french,german,...,koreana,schinese,tchinese`). PatchSmith:
- recognizes the columns from the language's name or code (`english`, `en`, `English (US)`, `koreana`, `schinese`, `textEN`…) and requires a column that is not a language, the key (for example `id`): without it, the game might look texts up by their English value, and the table is not touched;
- translates the original language's column in place: in the game that language stays selected. An Italian column already filled is the official translation (an empty column is not: it is a planned but untranslated language);
- rewrites only the translated cells: separator (`,` `;` or tab), quotes, BOM and line breaks stay as the game wrote them, and the file is read back to check. Many games read the table by splitting the lines on commas (in Megaloot no English text has a comma, `;` is used instead): if the table does not use quotes the CSV way, in the translations `,` becomes `;` and `"` becomes `'`, as the game's texts do. A translation with an extra line break stays in the original;
- skips a `.csv` with its `.csv.import` next to it (Godot source: the game uses the compiled file).

**Without recognized language files** PatchSmith proposes the experimental translation of the text files it finds (see "How it works"): the profile remembers that the game is GameMaker, so there too translations adapt to the game's fonts and the one-word labels of `{key: text}` files are translated.

**Limits:**
- Italian is longer than English: some texts may go slightly beyond their room (the log lists them);
- texts written in the game's code (inside `data.win`, or in the executable for YYC-compiled games) stay in the original;
- other localization formats (INI, UTF-16 JSON files) are not handled yet.

## Official language: when the game already has it

Before translating, PatchSmith checks whether the game **already has the official translation** in the target language. If it does, it does not make a machine translation, which would be worse: it uses the official one.

**How it finds out:**
- **Steam:** for a game installed from Steam it reads, offline, Steam's cache (`appcache/appinfo.vdf`) with the official languages. It recognizes the install even through junctions and links.
- **A folder that is not a Steam install:** the game can be recognized by comparing the folder's executables with the games' launch paths in Steam's cache. The recognition is cautious:
  - installers (vcredist, DirectX, .NET...) and generic names (`start_protected_game.exe`, `game.exe`, `launcher.exe`...) do not count;
  - an executable launched by several games does not count;
  - on a tie nobody wins.

  Even so it only counts if the game's files say nothing **and** the folder's name resembles the game's; otherwise it is just a note, which does not block the translation.
- **The game's files**, engine by engine:
  - Unreal: per-language `.locres` in the paks, including those of UE 4.26+/UE5 if they are not encrypted; only the game's texts count, not the engine's or the plugins';
  - Unity: active languages of I2 Localization and Unity Localization;
  - XNA/.NET: language satellites;
  - GameMaker: per-language files (also JSON with comments and the code attached to the name, like `stringsIT.json`) and filled columns of CSV tables.

Variants matter: for Chinese the script (zh-TW and zh-CN are different languages and are never swapped); for the other languages the exact code is preferred, then the language without region.

**What it does:**
- **Unreal:** it sets the language in the user's `GameUserSettings.ini`, `[Internationalization]` section. It only changes the `Language`/`Locale`/`Culture` keys already there, otherwise it adds `Culture`. It uses the exact name of the language folder, for example `it-IT`. It looks for the file in `%LOCALAPPDATA%\<Project>`, in the game's folder and in `Documents\My Games\<Project>`. It only does so with the game closed, and does not write if the game has its own language setting: in that case it gives instructions. Each line keeps its own line ending, even when they are mixed in the file.
- **Unity with I2 Localization:** it sets the "I2 Language" preference, in the Windows registry, to the language's exact name (for example "Italiano"). It only touches that value, with the game closed, and only if the game has already been started once. If the game changed I2 to save the language its own way (for example `LastSelectedLanguage`), it touches nothing and gives instructions. Even when it sets it, it is an attempt: if the game starts in another language, it must be chosen in its menu.
- **Steam:** it says where to choose it. The "Properties > General > Language" menu only appears if the language can be selected from Steam; otherwise it must be chosen in the game's options. PatchSmith does not change Steam's files.
- **In the other cases:** it explains exactly how to turn it on.

Every changed setting is recorded in `languages/`. "Restore originals" puts it back as it was, line by line, but only where the game or the user did not change it in the meantime. It does this last: if it cannot (game open, read-only file), the rest of the restore goes on and the record stays for a retry.

If it does not find the game's settings, the first time it asks to start the game once. The following times it says to choose the language in the menu.

If a PatchSmith machine translation is still installed, the message says so.

To translate anyway: `--translate-anyway` from the command line, or "Yes" in the interface's question. In that case the official language check is not done, and a language set earlier by PatchSmith goes back to how it was, so the installed translation shows. `smith auto` exits with 0 also when it handled the official language.

PatchSmith **does not change** unofficial components, such as files that replace Steam in non-original copies.

## Configurator: game settings changed from outside

Some games have Italian inside them, but no setting to choose it. The configurator changes what is needed **in the game's own files**, always reversibly:
- **verified backup** next to every touched file (`.patchsmith-originale` + `.patchsmith-info`, as for translations): "Restore originals" puts everything back, even without a record;
- **record** in `configurations/`: what was changed, when, and the consents given;
- **all or nothing**: if a file cannot be written, those already written go back to how they were;
- **with the game closed**, checking free space and read-only files;
- if a game update replaces a file, the new file becomes the original (the old backup is kept) and the change is reapplied.

**Changes to executables.** They are made only with your consent: PatchSmith asks the first time for each game, then remembers it (for example to reapply after an update). From the command line: `smith auto <folder> --modify-executables`. The digital signature of changed executables is no longer valid: if Windows' Smart App Control is on, PatchSmith does not change them, because Windows would block them. Some antivirus might flag them.

Knowledge about individual games lives in **profiles of games** (`patchsmith/games.py`). Each one finds the changes by a verified **byte pattern**, never by a fixed address: a different version of the game is refused without touching anything.

### Game profile: KINGDOM HEARTS HD 1.5+2.5 ReMIX
- **How it chooses the language:** each of the 6 programs (KH1, Re:CoM, KH2, BBS, launcher menu, Theater) asks the platform (Steam/Epic) for the language and converts it into a code (English 1, German 3, French 4, Spanish 5, Italian 6). For any other answer it uses a default value: English. It has no other language setting. The texts of all these languages are already installed.
- **What PatchSmith does:** it changes the default value, 1 byte in each of the 6 executables. So the answer "English" leads to Italian, while French, German and Spanish chosen by the platform stay respected. The game works as with Italian chosen in Steam: Italian texts, menus, fonts and images with writing; the voices stay the original ones.
- **What it does not touch:** the protected launcher (`ReMIX.exe`) and Steam's components.
- Before the first change it copies the saves to `save-backups/` (read only).
- If you have the game installed from Steam with Italian selectable, PatchSmith points you to Steam's menu instead of changing the executables.

Coming with the same framework: resolution, windowed/fullscreen mode and FPS limit (for Kingdom Hearts they are in `config1525.dat`).

## What works today

| Engine | Status |
|---|---|
| Electron / NW.js / HTML5 | **Supported**: JSON data + menus (injector). Tested on Sudoku Mansion |
| RPG Maker MV/MZ | Experimental, after asking for confirmation (`www/data/*.json` + canvas menus). Its events mix dialogs and plugin commands, often with Japanese words used as identifiers: without a dedicated adapter, translating them can break the game |
| Unity (Mono, 32/64-bit) | **Supported**: automatically installs and configures BepInEx + XUnity.AutoTranslator, which translate the texts inside the game while you play. Tested on Rubinite (Unity 6) |
| Games with encrypted files (e.g. Electron with GemShell) | **Translated while you play**, if the game has a plain-text `resources/app/preload.js` running in its page (`contextIsolation:false`, `sandbox:false`, as with GemShell). The encrypted file is never opened or touched. At the end of `preload.js` (with a backup) PatchSmith adds a script that translates the page's texts: those already translated from `patchsmith-dizionario.json`, new ones requested from PatchSmith's translator, which starts and closes with the game. Numbers are removed and put back ("{{A}} minutes"); accented letters the fonts lack become e', a'. A screen's new lines are requested together and the local translator does them in parallel (test: 40 lines in about 4 seconds). A pending line stays hidden at most one second, then the original shows until the translation arrives. Without that `preload.js` the game cannot be translated, and PatchSmith explains so without touching anything. Tested on Now That's a Big Dragon (on-screen text test). Steam emulator folders (`steam_settings`…) are never scanned |
| Unity with Unity Localization (also IL2CPP) | **Supported** for IL2CPP games: it translates the English text tables inside their bundles, without mods, even when the catalog checks the bundles' checksums. Also with the binary catalog (`catalog.bin`). In Mono games it adds to XUnity, for animated dialogs. Tested on SWORN; Big Dig with `catalog.bin` |
| Unity IL2CPP without Unity Localization | Detected. Coming (needs BepInEx 6) |
| Unreal Engine 4 (unencrypted paks) | **Supported**: it translates the `.locres` files and adds them as a patch pak, without touching the original paks. Tested on Dungeon Defenders: Awakened |
| Unreal Engine 5 / IoStore, encrypted paks | Detected. Not supported yet |
| XNA / FNA / MonoGame with localized .NET resources | **Supported**: adds translated satellites, with characters adapted to the game's fonts. Tested on Rogue Legacy |
| XNA / FNA / MonoGame without localized resources | Detected. The game's text files (JSON, CSV): experimental, with characters adapted to the game's fonts. Texts in the code: coming |
| GameMaker with external localization files | **Supported**: translates the original language's files with a backup, adapting characters to the game's fonts: line files (Dokimon), per-language JSON dictionaries (Forager; also with sections, comments and the code attached to the name, like Another Door) and CSV tables with one column per language (like Megaloot) |
| GameMaker with texts only in the code (data.win) | Detected. Coming |
| Godot 4 | **Supported** if the game uses Godot's translation tables (`.translation`, imported from CSV or PO), even with the data pack embedded in the executable. PatchSmith reads the English table from the pack without touching it: compressed texts (smaz), keys as hashes. Then it translates and puts next to the executable `override.cfg` (Godot's intended way to change the project's settings) with a small script that, at startup, replaces the English table with the bridge to Italian and adds the Italian language. BBCode color codes (`[color=...]`) protected. Encrypted pack: it is not opened. Godot 3: coming. Tested on You Know The Drill (Godot 4.4) |
| Ren'Py, KiriKiri, Wolf | Detected. Adapters coming |

**Limits:**
- Machine translation has no context: the quality is machine-translation quality.

## Translators

| Translator | What it needs | How much | Notes |
|---|---|---|---|
| **Local translator** (recommended) | A one-time install: Ollama (1.5 GB) and a Google TranslateGemma model (4B: 3.3 GB; 12B: 8.1 GB). Best with an NVIDIA graphics card | **No limit** | It translates on your PC: no account, no quotas, no blocks; after the install it also works offline |
| **DeepL** | Your own free key: a "DeepL API Free" account on deepl.com/pro-api (it asks for a card for verification, it charges nothing) | 500,000 characters a month (Forager: about 42,000) | Official API, the best quality for Italian. Clear errors: wrong key, monthly characters used up. The **Check** button next to the key tests it right away and says how many characters you used this month |
| **MyMemory** | Nothing (no account); with your email the limit goes up to 50,000 characters a day. The email is not confirmed: it only raises the limit. **Check** verifies that it is written correctly and that MyMemory accepts it | 5,000 characters a day without email, 50,000 with | Official public API. One request per line: it is slower |
| **Free Google** | Nothing | Not stated | **Unofficial**: Google does not intend it for programs (its `robots.txt` excludes it) and since September 2026 it often refuses these requests from the very first one. In the menu it is called "Free Google (unofficial)"; when you choose it a dialog explains the blocks (once per session) |

**Local translator.**
- **What it installs:** Ollama 0.40.4 (official release from GitHub, MIT license, SHA-256 checksum verified) and the chosen model, from Ollama's official registry. Gemma models have their own license (Gemma Terms of Use), which you accept by downloading them. Everything goes into `components/ollama`, next to PatchSmith: nothing is installed into Windows. PatchSmith remembers where it is (`%LOCALAPPDATA%\PatchSmith\local-translator.json`): another copy of PatchSmith on the same PC, or the next version extracted into another folder, uses the same one instead of downloading the gigabytes again (a complete installation of the copy's own comes first). The server only starts while translating, only answers this PC (127.0.0.1, its own port) and closes at the end, even if PatchSmith closes badly. It does not use or touch an Ollama that is already installed.
- **The two models**, tested on 100 Forager texts with an RTX 3060 Ti 8 GB:

  | Model | Time per text | Whole Forager (about 1,700 texts) | Quality |
  |---|---|---|---|
  | **Quality (12B)**, default if downloaded | about 2.5 s (62% of the model fits in the graphics card, the rest in memory) | about 1 hour | Fewer mistakes on item names and grammar |
  | **Fast (4B)** | about 0.5 s (all in the graphics card) | about 15 minutes | Good on sentences, more mistakes on short names |

  Before starting PatchSmith shows the estimated time, and after the first lines it recalculates it on your PC. If the model is slow (for example without an NVIDIA graphics card), it says so and recommends the Fast model. Before installing, the interface suggests the model suited to the graphics card's memory (12B from 8 GB up, otherwise 4B); after the install a test sentence says whether the model fits in the graphics card and how fast it is.
- **Context:** the model translates one line at a time, with a fixed context ("text of a video game, address the player informally"). In language files with official translations (e.g. Forager) it also receives:
  - the text's key, without the final number (`configWeather`, `itemName`), as a hint of where it appears;
  - up to 3 of the game's official translations, only to understand the meaning. For Italian French, German and Spanish are used, but not Spanish and Portuguese together, because the model copied them. If the lines do not match, the model does not receive them.
  - If the answer is identical to an official translation, it retries without references, and keeps it only if it comes back the same: "corona" is fine, "saque" is not. An answer with letters Italian does not use, such as "crânio", is discarded too.

  In the tests on 48 hard Forager texts the good translations went from 43% to 64% with the 4B and from 73% to 80% with the 12B ("weather" becomes "meteo", "fossil" "fossile", "exit" "esci").
- **Checks on every translation:** PatchSmith removes what the model adds on its own:
  - quotes and notes in parentheses;
  - several alternative translations for a name ("edificio; costruzione");
  - the final period or the dots where the original has none.

  If the original starts in lowercase, the translation stays in lowercase too. It discards instead answers with surrounding text ("Translation: …"), refusals, the wrong script, changed numbers, repetitions, extra line breaks (or missing ones, in a multi-line text), anomalous lengths and lost placeholders.
  - A discarded answer is retried with different parameters. If it had changed a number, it retries with the instruction to keep them as they were ("200% more" translated "il doppio" becomes "il 200% in più").
  - Small numbers written in words are accepted only with unambiguous words: "1 or more" translated "uno o più" is fine, but "un" and "sei" are not.
  - Codes damaged by the model (`[[4]Spine`, `[0]`, `[[2]〕`) are put back in order. Line breaks the model adds to a one-line text become spaces. If a code is missing, it retries with the instruction to copy them all; there is a third attempt for texts discarded twice.
  - A value with a sign at the start (`-1 Dash Charge`, `+25% damage`) is protected like a code.
  - If only one placeholder is wrong, the pieces between the codes are translated separately.
  - If it still does not work, PatchSmith retries right away in another way (see "Discarded texts and translations to redo").
- **Several lines together:** with the Fast model the graphics card translates 4 lines at a time, about 1.3 times faster (measured on an RTX 3060 Ti: 0.10 s per line against 0.14). The Quality model, which does not fit entirely in the graphics card, goes one line at a time.
- **Do not start the game while translating:** the graphics card is busy. **Stop** interrupts between one line and the next, and the translations made stay saved.

**Discarded texts and translations to redo** (applies to every translator; seeds only to the local one). A text discarded by the checks used to be retried with the same parameters, and the answer was the same: "run it again" did not help. Now:
- **Right away, in the same run**, discarded texts are retried in another way: each sentence and each piece between codes on its own, with new seeds (derived from the text and the attempt number, and slightly higher temperatures: at temperature 0 the seed changes nothing). The model stays the one you chose: PatchSmith does not load the Quality (12B) model on its own instead of the Fast one. The texts that remain can be re-translated, if you want, with the 12B from "Fix translations".
- Those that stay in the original go into `memory/<game>.it.problemi.json`, with the reason (for example "discarded by the checks (different numbers)") and the attempts made. At the next "Analyze and translate" they are retried **with entirely new seeds**. The final dialog says what PatchSmith already tried and lists them with "Show remaining".
- **Translations that cannot be written into the game** (characters the game's fonts lack, a shape different from the original, extra lines): they go into `problemi.json` with the reason. If PatchSmith had written them, with "Analyze and translate" it redoes them **right away** with the right instruction (for example "do not use: →"); if "Reapply", which does not translate, finds them, it redoes them at the next translation. The previous translation stays until a better one arrives. If you had fixed them yourself, they stay in the memory and the final dialog tells you to fix them with "Fix translations".
- **Saved translations checked again:** at every translation the entries written automatically (also by earlier versions or by Google) are checked again with the current checks: letters or words of another language (not counting tags, codes and titles in quotes), numbers, lines, placeholders, a translated game name, a copied official translation of another language. Those that fail are redone, starting from new seeds (with the same ones as the first round the answer would be the same); if redoing them fails, or the new translation still does not pass, it stays noted in `problemi.json` as suspicious, even if it can be written into the game, with the attempts made. Your corrections are never touched. After 6 attempts a translation is left as it is. A copied official translation only counts for sentences: a word identical in two languages ("Oro" in Spanish and Italian) is normal.
- **Unexpected problems:** an unexpected translator error during the new rounds does not stop the work: the translations made are written into the game and the remaining texts go into `problemi.json`.

**Shared memory of all games** (`memory/comune.en-it.json`, applies to every translator). Besides each game's memory, PatchSmith keeps the **short texts** (up to 4 words: "options", "back", "press any key") already translated for other games, and does not translate them again.
- Long sentences do not go there: across different games they are almost never repeated identically, and their meaning depends on the game.
- Only the texts **translated in that run** that pass the current checks enter it (not the old entries of the game's memory) and the corrections made by hand. Next to it, in `comune.en-it.origine.json`, there is where each entry comes from: translator, model, PatchSmith version and game, or "corrected by hand".
- Entries without an origin (put there by earlier versions) are used only if they pass the current checks; otherwise the text is translated again and the entry replaced. An entry that translates a game's name (for example its title) does not apply to that game. The checks look at the form, not the meaning: "Obsidian" translated "Obsidiano" passes, and must be fixed by hand.
- If you fix a short text with "Fix translations", the correction goes into the shared memory right away and the games translated afterwards use it (also a text left untranslated that you translated yourself; an entry already fixed by hand in another game stays that one). A correction identical to the original (for example a name left in English) only applies to that game.
- No single-word dictionaries are used: a sentence translated word by word comes out badly ("light" can be "luce" or "leggero").

**How PatchSmith avoids getting blocked by online services.**
- **A cautious limiter**, shared between the interface and the command line:
  - Google: at most one request every 4–6 seconds, 8 a minute, 60 an hour;
  - before starting it shows how many requests are needed and how long it will take.
- **At the first refusal it stops right away**, without insisting, and remembers until when not to retry:
  - refusal at the first request (access not accepted, not a limit exceeded): 1 day, then 3, then 7, then the service stays disabled until you turn it back on;
  - refusal after successful requests (rate limit): 1 hour, then 6, then 24.

  Insisting during a block can make it longer. PatchSmith does not look for other ways around a Google block: to continue right away you choose another translator, preferably the local one (the block messages recommend it, and before "Analyze and translate" a paused translator proposes "Use the local translator").
- The translations already made always stay saved: work resumes from where it stopped.

## Roadmap

- **Phase 1:**
  - Unity IL2CPP (BepInEx 6 + XUnity IL2CPP);
  - Ren'Py (`tl/` + unrpyc);
  - RPG Maker (rvpacker-txt-rs);
  - web games inside `app.asar`.
- **Phase 2:** Godot 3 and Godot texts outside the translation tables; texts in GameMaker's code (`data.win`).
- **Phase 3:** Unreal 5 / IoStore and the Japanese visual novel engines.

## Tests

**All the tests together:** `python -I test/run_all.py`. Each test gets its own empty folder, with its own profiles, memory and settings inside (a temporary `PATCHSMITH_DATA` folder just for it). `%LOCALAPPDATA%`, `%APPDATA%`, `%TEMP%` and the user folder are test folders of its own too. So the options, saves and crashes of real games are never touched, and a test does not see what another one left behind.
- Each test's output goes into `logs/<test>.txt`, in the work folder. A test lasting more than 15 minutes is stopped, together with the programs it started.
- At the end it checks that PatchSmith's real data (profiles, memory, settings…) did not change.
- The work folder is deleted if everything passes; `--keep` keeps it, `--work <folder>` chooses it (for example on another disk).
- With names it runs only those tests: `python -I test/run_all.py test_unity test_xna`.
- `test_gui` runs on a fake web game created by the test (`test/fake_web_game.py`): PatchSmith's window opens for a few seconds. It starts with settings already saved, which at the end must still be as they were.
- `test_safety` needs a **copy** of a game: `--safety <copy> <relative file.json>`. Without the copy it is skipped, and says so.

The individual tests:
- `python -I test/test_regressions.py <empty-folder>`: problems already solved on real games, so they do not come back. Covers:
  - Unity 6: BepInEx starts at the first MonoBehaviour, Unity 2022 is not touched, the existing configuration is kept;
  - IMGUI interfaces (also in a game module or in `Assembly-UnityScript`, not in third-party libraries), the game's DLLs and keys written in XUnity's configuration;
  - a Unity Localization catalog that checks the bundles' checksum (CRC), also with the text at an odd position;
  - numbers with a decimal point ('2.5') in the local translator's check;
  - key names in web games (`<kbd>`, `class="key"`), with node.js also on the real injector;
  - XUnity's test options always off in the configuration;
  - the real logs, trimmed and anonymized, in `test/data/signals/` (see their `README.md`), also Windows crash events and the lines journal.
- `python -I test/test_checks.py <empty-folder>`: the after-play check, with fake games and the trimmed real logs of `test/data/signals/`. `%TEMP%`, the user folder and `%LOCALAPPDATA%` are test folders. Covers:
  - Unity: a startup crash of the right copy (the other copy's, in the same folders, does not count) fixed with BepInEx's entry point; `LogOutput.log` without "startup complete"; Google stopped in `Player-prev.log`; a translator command with a Python that does not exist; changed configuration; a journal with temporary lines (cache lines replaced, the line fixed by hand stays) and lost lines put back in the memory; temporary files; everything read-only without writing anything, with the game open without writing anything, then fixed; a second check that does nothing; a crash even after the fix that proposes "Restore originals" without doing it;
  - a game installed with an old version (like Rubinite): Google paused, stated with the date, PatchSmith's translator put back, version updated, "Install the local translator" if there is no translator;
  - files removed by the antivirus put back once, and to do if they go missing again;
  - web game: files put back to the original by the game (Reapply on its own), updated ("Analyze and translate"), crash in the Windows log ("Restore originals" to confirm; real events 1000 and 1026 read also in the Windows encoding);
  - GameMaker (added language not chosen or removed), Unreal (language and pak not loaded), XNA (satellite removed, game updated);
  - `smith.py check --dry-run` (code 2, nothing written), the outcome dialog, the report and the `<game>.controllo.json` file, which is not a profile and is not deleted;
  - the cases found in the review: logs over 2 MB (a long session is not a startup crash, the errors at the end of `Player.log` count), interrupted install (only "Restore originals"), a profile without a folder, an Unreal log with the project folder, Windows crashes from before the last check, PatchSmith updated, a live translator started without translators, a command line with a damaged profile (the other games are checked, code 1);
  - the check at opening that does not re-translate the journal's lines (they stay to do with "Re-translate", the journal does not advance, lost lines are saved anyway) and then re-translates them with the button; a Unity 6 game with an incomplete `LogOutput.log` and no crash (just for information), with a crash in the Windows log ("does not accept mods" to confirm) and "even after the fix" only if the fix happened;
  - "Fix translations" corrections saved with the Unity game open, written by the check (with the game closed, once; a line changed by hand stays), a different version with the configuration to put back said in a single message, the report with pending corrections and the fonts' letters; "Re-translate" with the local translator not installed (lost lines are saved, the button is "Install the local translator").
- `python -I test/test_preliminary.py <empty-folder>`: the checks before starting and the safe defaults, with simulated running processes, free space, Steam and graphics card. Covers:
  - game open (also for Reapply and Restore), unknown, identical or out-of-list languages, a folder that cannot be written (also inside Program Files; the test file does not remain), little space, Google paused (Install or Use the local translator), local translator not installed (and continuing without it: it stops before touching the game);
  - the same game in another folder: identity from Unity, Steam, executables and name (without the programs many games share, such as `createdump.exe` or DOSBox); a failed copy of the translations does not stop "Analyze and translate"; a copy reusing the original's memory (a correction by hand too), a moved game bringing its profile along, no proposal for another language or an empty memory;
  - local translator as default even when not installed; video memory from `nvidia-smi`, the Windows registry and WMI (at the 4 GB limit it is unknown; `nvidia-smi` and PowerShell never from the current folder) and the suggested model; `smith.py local` with the suggested model and `--install` with the test sentence;
  - the test sentence after the install (graphics card, processor only, sentence left untranslated, server that does not start) and the block messages recommending the local translator.
- `python -I test/test_legacy.py <empty-folder>`: the files written by earlier versions, with Italian keys and codes (`patchsmith/legacy.py`). Old game profiles, settings, check states, journal lines, records, backup information and manifests read with the English names, the maps of files and texts left as they are, the new files unchanged; PatchSmith's readers with old files (game profile found by its folder, backup information, the BepInEx plugin's state and command); `migrate_formats` rewrites profiles, records and settings keeping each original as `.italian-keys`, never the translation memories.
- `python -I test/test_user_data.py <empty-folder>`: the data folder (`PATCHSMITH_DATA`). Checks the paths with and without the variable, `smith auto`/`status`/`restore` on a fake game, the Unity live translator's command, saving the settings and the move from the Italian folder names to the English ones (moved without losing anything, components merged, profile requested with the old path).
- `python -I test/test_run_all.py <empty-folder>`: the program that runs all the tests. Checks that every test has its own data and folders, that a stuck test stops together with the programs it started, that the real-data check notices every change and that the list of tests without arguments is right.
- `python -I test/web/make_page.py`: generates `test/web/index-inline.html`, the page that tests the injector in the browser (dynamic texts, attributes, `<option>`, buttons also of one word like `PLAY` and `OK`, canvas, HTML entities).
- `python -I test/test_formats.py <empty-folder>`: the language file formats of new games and their engines' limits, with fake games built on the model of real ones (no real game text). Covers:
  - JSON with `//` and `/* */` comments, trailing commas and sections (Another Door style): read, rewritten identical byte for byte except the translated sentences (also BOM, CRLF, numbers such as `1.50`, `\u` escapes), a changed non-string value that is not written, damaged files that stay errors;
  - GameMaker with `localization/stringsEN.json` and the official translations: code attached to the name (`stringsEN`, `stringsBR`, `stringsSC`, `stringsTC`), the `strings{L}.json` family, one-word labels and `[yellow1]…[/c]` tags translated, a Japanese font and `scribble_fallback_font` that do not take `è` and `ò` away, only the English file changed, identical restore; with `stringsIT.json` the official language;
  - CSV tables: Megaloot style (split on commas, `koreana`, `schinese`, `tchinese`, empty Italian column, `,` that becomes `;`) and a real CSV table (BOM, `;`, CRLF, quotes, `""` and line breaks in cells), rewritten changing only the translated cells (also a hand-split table with a cell like `"Hero" rises`, and with empty lines between groups of texts); tables without a key, with a single language or not UTF-8 refused; a GameMaker game with `translations.csv` translated and restored (a translation with an extra line break stays in the original, written in the log); the filled Italian column as official language; the table in a web game and the Godot source skipped (also in the log);
  - GameMaker without recognized language files: experimental translation with the engine in the profile (also in old profiles), single words translated and characters adapted to the font (`è` → `e'`); the same for XNA, with simulated fonts; a list of objects (like the sounds' `PluginInfo.json`), a save and a family of per-language files with a list of objects do not become language files (identifiers are not translated);
  - GameMaker fonts for another script (Japanese with a few accents), fallback fonts and fonts without any letter of the official translations excluded; a font with Cyrillic and a few accented letters (like Another Door's) and a Latin font without accents that still count;
  - the `test/web/index-inline.html` page with the PLAY and OK buttons, and the visible JavaScript strings (`textContent`, `fillText`, `label:`, `add.text`) told apart from identifiers, classes, fonts and comparisons;
  - Unreal without `.locres` (no profile, the reason), with `.locres` outside the archives (different reason), with an unreadable pak (skipped, profile created) and with a pak mounted in a deeper folder (translated into a patch pak with full paths);
  - the skipped files in the log: invalid or too large `.json`, files with few texts, web files too large.
- `python -I test/test_font_length.py <empty-folder>`: letters the fonts lack and labels that are too long, for every engine, with fake games, fonts and logs (`test/fake_unity.py`, `test/data/signals/`; no real game, no network, fake translators in memory). Covers:
  - Unity fonts: a `.ttf`'s cmap table, the TextMeshPro fonts in bundles (with the type tree) and in `resources.assets` (without it, recognized by their shape), a dynamic font with its `.ttf`, no limit when it is unknown, the result kept in `font.json` and read again after a game update;
  - Unity Localization: `è` → `e'` only in the game's table (the memory keeps `è`, the French table does not change), a character that cannot be adapted (`→`) in "Not written to the game", the letters noted by the check valid even if a font has them, no change with a font that has every letter, an unexpected error while reading the fonts that does not stop the writing;
  - XUnity: `WriteUnityLog` turned on at install and put back by the check; the missing characters read from real lines (also `\U0001F41A` and the old format); the game's copy recognized also in IL2CPP games; the check that writes nothing in read-only mode, then adapts the cache lines written by PatchSmith (not the one changed by hand) and notes the letters in the profile; afterwards the cache and the new-lines translator avoid them; Unity Localization with `Player.log`: letter noted and table rewritten, those only in the game's texts ignored; "Reapply" writing adapted a correction made in "Fix translations" (and counting it as written); an unexpected error in the letters check that does not hide the other problems; a `BepInEx.cfg` with non-UTF-8 bytes rewritten unchanged;
  - Unreal: with French the full Latin-1 (also `ì` and `ò`), Japanese that does not count, a character no language uses (`★`) that leaves the original; without hints no limit;
  - labels: the room (with and without official translations), the too long ones requested shorter with the number of characters (also in the local translator's prompt), the shortest valid one kept, the one still too long in `problemi.json` with the original text and in the "Too long" list, only once, corrections by hand left alone, the online translators that only take notes (and the local translator that then requests them shorter, once), "Re-translate" with the room, an unexpected error that does not stop the translation, the outcome.
- `python -I test/test_gui.py <copy-of-a-game>`: automatic test of the graphical interface, with the test translator and the final restore. It also checks that during a job a second one does not start and that the translator's controls are disabled. Then it checks:
  - the end-of-operation dialog's title for every outcome;
  - the log saved in `logs/`, also with an injected unexpected error ("Unexpected error" dialog, traceback in the file) and with an error in a window command (`interfaccia` log also after installing the local translator, dialog without "Try again");
  - that a damaged profile does not leave the buttons disabled and that "Use the local translator" after an error repeats "Analyze and translate" with the same choices;
  - the chosen game's check (end dialog), the check at opening (no dialog, the bottom buttons only when there is something to do, `controllo` log; if you already started something else it starts as soon as that ends), "Details" with "Restore originals" and "Create report" for that game's folder, the warning about PatchSmith updated with the window open (only once);
  - "Restore originals" and "Create report" (the zip goes into `reports-gui-test`, next to the copy, never on the real Desktop; with that folder locked it goes into `logs/`);
  - the first start without a saved translator ("Local translator" to install with "Install…" and the model for the simulated graphics card), the checks before starting (game open and "Try again", protected folder, language, Google paused, local translator to install: the operation does not start), a copy of the game in `copy-gui-test` reusing the translations and the "Re-translate N phrases" button after the check at opening; the install requested before starting (with the test sentence working the operation starts again, otherwise a dialog and no operation) and the check at opening waiting for an open dialog to close;
  - "Fix translations…": a line fixed in the window and saved (valid memory with the new value, "Reapply" on its own changing the game's file) and the "Fix translations" button in the end dialog with texts left.

  Without `PATCHSMITH_DATA` profile and settings go into `data-gui-test`, next to the copy. The settings that were already in that folder go back to how they were at the end.
- `python -I test/test_package.py <empty-folder>`: the release package (about 40 seconds): program, `PatchSmith.exe` and portable Python with Tkinter, without any user's data or downloaded components; the zip with only the `PatchSmith` folder; `smith.py` with the portable Python; `PatchSmith.exe` choosing the portable Python even with an installed Python; a package with data inside that is not deleted.
- `python -I test/test_paths.py <empty-folder>`: the complete paths of someone using only the interface, from start to end, with fake games and fake translators (no network, no Ollama, no real game); PatchSmith's window opens for a few seconds, and halfway it closes and reopens. Covers:
  - a new web game: "Analyze and translate", the after-play check, "Fix translations" with the correction written into the game, "Restore originals" (identical files) and "Reapply", "Create report";
  - the game open before starting ("Try again") and during the job (GameMaker: translations ready but not written), Google paused ("Continue anyway", then "Use the local translator"), Google blocking halfway ("Translation interrupted"), the local translator not installed ("Continue anyway" without touching the game, then "Install now" and the translation starting again on its own), texts left untranslated ("Fix translations" on the right list);
  - a GameMaker game with a translation the fonts cannot draw (the end dialog and "Fix translations" lead to "Not written to the game");
  - a Unity Mono game: PatchSmith's new-lines translator and Unity's log turned on; a correction saved with the game open; when PatchSmith reopens, the check with the trimmed real logs (Google stopped, letters the font lacks) that fixes on its own, writes the pending correction and proposes "Re-translate"; "Details", "Restore originals", "Reapply" and the report;
  - every dialog shown says the next step in Italian, without technical names.
- `python -I test/test_corrections.py <empty-folder>`: "Fix translations" with fake translators (no network, no Ollama) and fake games; PatchSmith's window opens for a few seconds. Covers:
  - the list and its filters (left untranslated, too long with the room measured in the game or without, not written to the game, numbers and placeholders, another language, suspicious such as "Esportare" instead of "Esporta", fixed by hand), the search and the checks while you type;
  - the window: title, counts, edited line, Esc, Ctrl+Enter and the other keys, an empty translation or one with a wrong placeholder, closing with unsaved corrections, a 750-line list in pages;
  - "Save and apply to the game" on a web game: valid memory, previous machine translations kept, shared memory as a correction by hand, text removed from the untranslated ones, "Reapply" on its own changing the game's file;
  - GameMaker: "Too long" with the room of the game's versions and a correction with a character the fonts lack ("Not written to the game" and the button in the end dialog);
  - Unity: XUnity cache lines in the list (left untranslated, the journal's temporary ones), replaced when saving while all the others stay identical, a correction postponed with the game open, a line changed by hand after the correction that stays, re-translation of a cache line;
  - "Re-translate selected" (new requests also for lines fixed by hand, and new again when pressed again; choice of the model, missing local translator), "Re-translate all with Quality (12B)" and "Start over" (only for the tests: the buttons are gone), a memory from an old version with few noted entries (only real corrections are "Fixed by hand"), "Open the file (advanced)" with Notepad, a damaged translations file, the "Fix translations" button in the end dialogs.
- `python -I test/test_outcomes.py <empty-folder>`: the outcome of every operation and its dialogs, with fake games (web, GameMaker, Unity Mono, IL2CPP and Unity Localization) and the test translator. Covers:
  - translated, partly (with the texts left to show), reapplied, restored, already original, nothing to reapply;
  - game open (no file touched), Google paused written in a test translators' state (no request), official language;
  - Unity IL2CPP without tables, Unity Localization with the checksum (CRC) checked (without "needs BepInEx 6"), anti-cheat;
  - errors turned into outcomes (unexpected ones too) and a dialog with a different title for every outcome;
  - the local translator's buttons ("Use" if it is installed, "Install" if not) and the dialogs of operations that do not touch the game (report, install: no "Try again");
  - "Create report": the expected files are there, the DeepL key and the email are not (not even inside a log or a web address);
  - who translates new lines while you play (Google, PatchSmith's translator, nobody, with the explanation and "Install the local translator") and a damaged file in `profiles/` (skipped);
  - no text reaching the interface points to `smith.py`; the command line writes the fixing command under the error and in `smith services`;
  - translations the game could not display, discarded when writing: the end dialog and "Fix translations" lead to the "Not written to the game" list.
- `python -I test/test_xunity_service.py <empty-folder> [<_Data folder of a Unity Mono game>]`: the new-lines translator for XUnity with test translators (no network, no graphics card). Covers:
  - answers, intact `{{A}}` placeholders, memory, lines journal, port already in use by the same game, closing together with the game's process (503 while closing; port freed right away even with a line being translated, which finishes and is saved), old `.tmp` files removed at startup;
  - translators in a row (one not answering or paused is skipped; 500 only if nobody answers) and which ones the service would use;
  - lines discarded by the checks: never 500, a fallback answer (also in pieces between the `{{A}}`) or the original, and what the journal says about it; the answer's language (Italian sentences with "se", "tu", "su", "no" not mistaken for Spanish; a suggestion left in English is);
  - Google with the cautious limits full (line postponed, no request) and paused; DeepL asking to wait two minutes (a single retry after a few seconds);
  - memory saved together by two programs and by 4 processes (no entry lost, removed entries that do not come back), shared memory, damaged file, lock (also a `.lock` that cannot be opened for a moment);
  - `CustomTranslate` configuration (without anti-spam, 2500 characters), Google, empty Endpoint with Google paused;
  - with the `_Data` folder, also the plugin's compilation, the command and the removal.
- `python -I test/test_unity_loc.py <empty-folder> [<bundle with the text tables>]`: Unity Localization, with fake bundles and IL2CPP games (`test/fake_unity.py`, no real game files). Covers:
  - uncompressed, LZ4 and LZMA bundles, also read in pieces (also an uncompressed block as large as the bundle, and an LZMA shorter than expected); truncated bundles; asset tables are not translated;
  - the language from the name, from the content (`english`, `localization_en`, `chinese(simplified)`) or from the language's name; Italian found this way as official language;
  - the catalog checking the checksum: only the translated bundle's goes to 0 (also with two bundles), catalog backup, Reapply, a new "Analyze and translate" (the catalog stays in the profile even if in the game it already has CRC 0), the catalog and the bundle put back by the game seen by the after-play check and translated again, an update changing only the catalog (the check removes the CRC again on its own), identical Restore; a catalog without checksums (like SWORN's) is not touched;
  - no file changed with `catalog.bin`, with the checksum not found, with the anti-cheat, without the source language (the reason states the game's languages; choosing one of them, it translates) or with Italian already in the game;
  - with a real bundle also the identical rewrite, the changed texts read back, the flow with the test translator on a copy and the identical restore.
- `python -I test/test_unity.py <empty-folder>`: the Unity adapter with a fake game and packages, without downloads (install, configuration, the user's mods, antivirus, interruptions, altered manifest, restore). Then who translates new lines with "Analyze and translate" and "Reapply" (with a fake plugin): PatchSmith's translator also when choosing Google and also after "Restore originals", the chosen translator saved in the profile, no translator available, missing C# compiler with Google free or paused. The pre-translation of today's Unity games: a game module with its strings (not third-party libraries), a compressed `data.unity3d` with a text across two blocks (not the images), an LZMA bundle, the English text table (not the other languages), a large file read in pieces, a damaged bundle, the bundles' time limit. Finally the game's names in the profile (from `app.info`; a name removed by hand does not come back) and XUnity's cache: wrong lines written by PatchSmith or by the live translator redone and replaced, lines changed by hand and names left as they are.
- `python -I test/test_unreal.py <empty-folder>`: the Unreal adapter with a fake game (v9 pak and `.locres` built by the test). Covers reading, translation, language change, other people's paks, altered manifest, interrupted install, signed paks, game update and restore.
- `python -I test/test_xna.py <empty-folder>`: the XNA/.NET adapter with a fake game, created by the test with Windows' C# compiler, and a fake XNB font. It checks the translations with .NET's real ResourceManager and covers fonts, accents, restore, other people's satellites, altered manifest, interrupted install and game update.
- `python -I test/test_placeholders.py`: protection of game codes, shared by every engine. Covers `{0}`, `%s`, `[Input:12]`, `\n` written as text, RPG Maker codes (`\C[3]`, `\nc<Name>`), GameMaker color and font tags (`[yellow1]`, `[/c]`, `[/]`) and Windows paths.
- `python -I test/test_limiter.py <empty-folder>`: the limiter and the online translators with simulated answers (no network, fake clock). Covers:
  - windows and pauses;
  - stop at the first 429 / "Sorry" page / redirect, pause remembered between runs, levels and disabling;
  - misaligned lines without bursts, network errors, Stop during a pause, time estimate;
  - DeepL (key, languages, 403/456/429) and MyMemory (email, 500 bytes, daily quota);
  - credentials read from the settings.
- `python -I test/test_local.py <empty-folder>`: the local translator without network and without a graphics card. It uses a simulated in-memory server and a simulated Ollama (`test/fake_ollama.py`) really started on 127.0.0.1. Covers:
  - the prompt's format, one request per line;
  - checks and second attempt (surrounding text, script, refusals, numbers, repetitions, line breaks, untranslated), truncated answers;
  - no false discards: "Ecco…", "Mi dispiace…", Chinese and Japanese, "1.000", wide digits, quotes of another style;
  - cleaning of what the model adds, and text left identical (retried once, then kept as a name);
  - context in the prompt (key and official translations line by line), a key copied by the model discarded, correction on numbers and numbers in words;
  - lost placeholders and re-translation in pieces, also with **Stop** during the fallback;
  - a line too slow stays in the original without stopping the others; a server that went down;
  - understandable errors (missing model, memory);
  - **Stop** halfway with what was done saved, and the graphics card freed;
  - time estimate;
  - server start and stop, with a clean environment (the user's `OLLAMA_*` variables do not pass through), a server that does not start, **Stop** during startup, a port taken by another program;
  - install: a zip with dangerous paths refused, a damaged folder rebuilt, a download resumed from where it was;
  - the message when it is not installed and the chosen model is not downloaded;
  - discarded texts: with a translator that fails with seeds 42, 7, 11 the text is translated in the same run (also with the simulated Ollama and its log of requests); a text always discarded goes into `problemi.json`, not the memory, the second run uses entirely new seeds and the final message does not say "run it again"; texts the Fast model discards do not make the Quality (12B) model load on its own;
  - the game's names: `app.info` with "Cup Dog Games" (translation identical to the original, no request, the old shared memory does not apply), an executable's version information, one-word names that are ordinary words;
  - shared memory: old entries that fail the checks skipped and replaced, only the texts translated now (with their origin), corrections by hand recognized;
  - saved translations redone (wrong numbers, another language) without touching corrections by hand, and left after 6 attempts;
  - translations the game cannot display (a character the font lacks): the automatic one redone right away with the instruction, the one fixed by hand noted and stated in the outcome, also after "Reapply";
  - redone saved translations that still fail (redone identical, or with writing into the game): new seeds at every run, noted as suspicious and left after 6 attempts; an unexpected error in the new rounds does not stop the writing into the game; the shared memory does not take a new translation in another language; "copied reference" only for sentences.
- `python -I test/test_gamemaker_json.py <empty-folder>`: JSON localization files (Forager style) and the Stop button. Covers:
  - recognition of languages and conventions (`*` = line break, `U` symbol), without false positives;
  - balanced line breaks, sentences split by `**` translated whole (but not titles and lists), empty `* *` lines, `{with spaces}` placeholders;
  - fonts: ASCII-only ones do not count;
  - only the original language's file is translated, with identical restore;
  - **Stop** during the translation: game not touched, work resumes from where it stopped;
  - the 429 wait interrupted right away and an understandable message.
- `python -I test/test_gamemaker.py <empty-folder>`: the GameMaker adapter with a fake game (line and block files, a second language, a minimal `data.win` with three fonts). Covers the format, the choice of files, fonts and accents, the complete flow with the test translator, identical restore and the case of a game that already has Italian.
- `python -I test/test_official_language.py <empty-folder>`: official language with a fake Steam (appinfo.vdf built by the test), a fake Unreal game with Italian (setting and restoring GameUserSettings.ini) and a fake Unity game with I2 (preference in the registry, in a test key that is deleted).
- `python -I test/test_configurator.py <empty-folder> [<Kingdom Hearts folder>]`: the configurator and Kingdom Hearts' game profile on a fake install (PE executables built by the test). Covers:
  - recognition and executables of different versions;
  - consent;
  - refusals: Smart App Control, game open, read-only;
  - failures halfway, with everything put back as it was;
  - game update and identical restore.

  With the real game's folder it repeats the test on **copies** of its executables: the game's folder is not touched.
- `python -I test/test_added_language.py <empty-folder>`: added language and lowercase games (Forager style), with a fake game: a minimal `data.win` (name, strings, an image font's sprite, `local/*.json` mask), also YYC-compiled (mask and map in the executable). Covers:
  - capitalization style, protected icons, lowercase, room for lines;
  - `local/italiano.json` created with English intact, no PatchSmith file the game would load as a language;
  - rerun, without mistaking the added language for an official one;
  - language set in the saved options (hex ds_map) and put back with "Restore originals" to an identical file; with the language in several places nothing is touched;
  - upgrade from an earlier version, which had translated english.json in place.
- `python -I test/test_language_regressions.py <empty-folder>`: the hard cases of the official language. Covers:
  - cautious Steam recognition: installers, generic names, shared executables, ties, junctions, damaged cache;
  - paks mounted in subfolders (v9 and v11);
  - Chinese by script;
  - GameUserSettings with mixed line endings, duplicate keys, damaged UTF-16, read-only and game open;
  - Unity: binary catalog, customized I2, isolated errors;
  - "translate anyway" with GameMaker and Unreal;
  - restore from the command line with the folder.
- `python -I test/test_cjk.py`: recognition of Japanese, Chinese and Korean texts. It also checks that nothing changes for the Latin script.
- `python -I test/test_safety.py <copy-of-a-game> <file.json>`: critical scenarios, to run after `smith auto <copy> --backend fake` (better with `run_all.py --safety`, which does everything; if the profile is missing, the test creates it). Covers the parent folder, the game update, the altered backup and the complete restore. Without `PATCHSMITH_DATA` profile and memory go into `safety-test-data`, next to the copy.

**These tests must only be used on copies, never on a real game.**

## Release package

`python -I smith.py package` builds, next to the program folder, `PatchSmith-rilascio/PatchSmith` and the zip `PatchSmith-<version>-windows.zip`: whoever downloads it extracts it and double-clicks `PatchSmith.exe`, even on a PC without Python.

- **What is inside:** the program (`PatchSmith.pyw`, `smith.py`, `patchsmith/`, the plugins' sources, `data/`, `LEGGIMI.md`, `README.md`, `docs/`, `LICENSE`), `PatchSmith.exe` and in `runtime/` a **portable Python**: only the parts of an installed Python that PatchSmith needs (Tkinter included; without tests, IDLE, pip), about 42 MB, 16 MB in the zip with Python 3.15. The `._pth` file isolates it from environment variables and the registry: another Python installed on the PC does not count.
- **What is not inside:** the data of whoever uses PatchSmith (profiles, memory, logs, settings…), the tests, the downloaded components (BepInEx, XUnity, Ollama: they are downloaded when needed, asking first).
- `PatchSmith.exe` uses the portable Python if present, otherwise the installed one. The translator that starts with the games (Unity, live web games) uses the Python PatchSmith runs with, so the portable one too.
- Before creating the zip PatchSmith tests the portable Python in an environment without Python (only Windows' folders in the PATH): it must start and have the modules it needs, Tkinter included.
- The zip contains a single `PatchSmith` folder: extracted over the previous one it updates it and leaves the data as it is.
- `--python <folder>` uses another Python (recommended for the release: the latest from python.org); `--output <folder>` changes where the package goes. A recent Python without installing it: from the version's `amd64/` folder on python.org (e.g. `https://www.python.org/ftp/python/3.15.1/amd64/`) download `core.msi`, `exe.msi`, `lib.msi` and `tcltk.msi` and extract them with `msiexec /a <file>.msi TARGETDIR=<folder> /qn` (extraction: nothing in "Programs" nor in the registry). The 0.1.0 package is built with Python 3.15.1 (Tcl/Tk 9.0.4); all the tests pass with both 3.15 and 3.11. A package someone already worked in (with data inside) is not deleted.

## Adding an adapter

An adapter must be able to do four things:
1. find the texts;
2. extract them;
3. put them back;
4. verify the result.

Look at `adapter_webjson.py` (data) and `adapter_webui.py` (web menus), and how `project.py` uses them. External libraries with a GPL license must only be used as external programs, to stay under the MIT license.
