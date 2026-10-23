# Real signals for the tests

Trimmed, anonymized copies of real logs, to test the after-play check without games: what went wrong and where. The
user's name is replaced with `giocatore`; the PC name and the hardware data are removed. The copies only lack lines
with no useful signal, for example the Windows libraries in the crash log.
`test/test_regressions.py` checks that they are there, that they contain their signal and that they stay anonymous.

| File | Where it comes from | Signal |
|---|---|---|
| `unity-player.log` | One Million Coins, `%USERPROFILE%\AppData\LocalLow\OyenUmiGames\One Million Coins\Player.log` | successful session: BepInEx, XUnity and PatchSmith's plugin loaded (`Chainloader startup complete`) |
| `unity-player-errore-google.log` | same game, `Player-prev.log` | live translator broken: `NullReferenceException` in `GoogleTranslateEndpointV2` and `Failed: 'MASTER'` |
| `unity-crash-player.log` | `%TEMP%\OyenUmiGames\One Million Coins\Crashes\Crash_<date>\Player.log` | crash at startup with Unity 6: `Crash!!!`, `AddComponent` inside `Application:.cctor` (BepInEx's entry point) |
| `bepinex-logoutput.log` | `<game>\BepInEx\LogOutput.log` | BepInEx started, plugins loaded, harmless HarmonyX warning |
| `servizio-xunity.log` | `components\ollama\servizio-xunity.log` | local translator for XUnity: starts, stops, memory count and `! traduzione interrotta` |
| `unreal-dds.log` | Dungeon Defenders Awakened, `%LOCALAPPDATA%\DDS\Saved\Logs\DDS.log` | PatchSmith's pak mounted (`zz_Patchsmith_it_999_P.pak`) but language forced by the game's settings (`Overriding language ... (en)`) |
| `bepinex-logoutput-crash.log` | the BepInEx lines of the crash's Player.log (`unity-crash-player.log`), which BepInEx writes identically to `BepInEx\LogOutput.log` | BepInEx started (`Chainloader started`) but never reached `Chainloader startup complete`: the game closed before |
| `evento-crash-1000.xml` | Windows Application log, `wevtutil qe Application /f:xml`, event 1000 of the One Million Coins crash (old copy) | crash with the executable's path (`AppPath`) and the module (`UnityPlayer.dll`); without PC name, Windows user and report number |
| `evento-crash-1026.xml` | same log, event 1026 (.NET) of a Windows program | .NET error with only the executable's name in the text ("Applicazione: …"), in Italian and in the Windows encoding (not UTF-8), as `wevtutil` gives it; stack cut to the first lines |
| `unity-tmp-caratteri-mancanti.log` | real lines from four `Player.log` of Unity games with TextMeshPro (Cast n Chill, Death Must Die, Moonglow Bay, Megabonk: `%USERPROFILE%\AppData\LocalLow\<company>\<game>`), with the start of the first one (IL2CPP game: the game's path is only in `Discovering subsystems at path`) | characters the game's fonts lack: `The character with Unicode value Ù was not found in the [slkscr] font asset`, also `\U0001F41A` (outside the basic plane) and a `‑` hyphen |
| `diario-richieste.jsonl` | built in the format of the live translator's journal (`memory\<game>.richieste.jsonl`) with One Million Coins sentences (`MASTER` is the one from the real `Failed:`) | every outcome: `rifiutata`, `rinviata`, `rilassata`, `errore`, `interrotta`, `ok` from the translator and from the memory, a sentence rejected and then translated, the last line half-written (game closed abruptly) |

`test/test_checks.py` uses these files for the after-play check: it copies them into the test folders in place of
the real ones (`%TEMP%`, `LocalLow`, the game's folder), changing the game's path written inside where needed.

The crash log and the successful session's log come from **two copies** of the same game, in different folders:
the game's `LocalLow` and `%TEMP%` folders are shared between the copies, so the signals must be attributed using
the game's path written in the log (`Mono path[0]`, `Symbol-SearchPath`).
