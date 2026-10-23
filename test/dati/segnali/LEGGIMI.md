# Segnali veri per le prove

Copie ridotte e anonime di registri veri, per provare senza giochi il controllo dopo una partita: cosa è
andato storto e dove. Il nome dell'utente è sostituito da `giocatore`, il nome del PC e i dati dell'hardware
sono tolti. Dalle copie mancano solo righe senza segnali utili, per esempio le librerie di Windows nel registro del crash.
`test/prova_regressioni.py` controlla che ci siano, che contengano il loro segnale e che restino anonime.

| File | Da dove viene | Segnale |
|---|---|---|
| `unity-player.log` | One Million Coins, `%USERPROFILE%\AppData\LocalLow\OyenUmiGames\One Million Coins\Player.log` | partita riuscita: BepInEx, XUnity e il plugin di PatchSmith caricati (`Chainloader startup complete`) |
| `unity-player-errore-google.log` | stesso gioco, `Player-prev.log` | traduttore dal vivo rotto: `NullReferenceException` in `GoogleTranslateEndpointV2` e `Failed: 'MASTER'` |
| `unity-crash-player.log` | `%TEMP%\OyenUmiGames\One Million Coins\Crashes\Crash_<data>\Player.log` | crash all'avvio con Unity 6: `Crash!!!`, `AddComponent` dentro `Application:.cctor` (punto d'ingresso di BepInEx) |
| `bepinex-logoutput.log` | `<gioco>\BepInEx\LogOutput.log` | BepInEx avviato, plugin caricati, avviso innocuo di HarmonyX |
| `servizio-xunity.log` | `componenti\ollama\servizio-xunity.log` | traduttore locale per XUnity: avvii, chiusure, conteggio della memoria e `! traduzione interrotta` |
| `unreal-dds.log` | Dungeon Defenders Awakened, `%LOCALAPPDATA%\DDS\Saved\Logs\DDS.log` | pak di PatchSmith montato (`zz_Patchsmith_it_999_P.pak`) ma lingua forzata dalle impostazioni del gioco (`Overriding language ... (en)`) |
| `bepinex-logoutput-crash.log` | le righe di BepInEx del Player.log del crash (`unity-crash-player.log`), che BepInEx scrive uguali in `BepInEx\LogOutput.log` | BepInEx partito (`Chainloader started`) ma mai arrivato a `Chainloader startup complete`: il gioco si è chiuso prima |
| `evento-crash-1000.xml` | registro Applicazione di Windows, `wevtutil qe Application /f:xml`, evento 1000 del crash di One Million Coins (copia vecchia) | crash con il percorso dell'eseguibile (`AppPath`) e il modulo (`UnityPlayer.dll`); senza nome del PC, utente di Windows e numero del rapporto |
| `evento-crash-1026.xml` | stesso registro, evento 1026 (.NET) di un programma di Windows | errore .NET con il solo nome dell'eseguibile nel testo ("Applicazione: …"), in italiano e nella codifica di Windows (non UTF-8), come lo dà `wevtutil`; pila ridotta alle prime righe |
| `unity-tmp-caratteri-mancanti.log` | righe vere di quattro `Player.log` di giochi Unity con TextMeshPro (Cast n Chill, Death Must Die, Moonglow Bay, Megabonk: `%USERPROFILE%\AppData\LocalLow\<azienda>\<gioco>`), con l'inizio del primo (gioco IL2CPP: il percorso del gioco è solo in `Discovering subsystems at path`) | caratteri che i font del gioco non hanno: `The character with Unicode value Ù was not found in the [slkscr] font asset`, anche `\U0001F41A` (fuori dal piano base) e un trattino `‑` |
| `diario-richieste.jsonl` | costruito nel formato del diario del traduttore dal vivo (`memoria\<gioco>.richieste.jsonl`) con frasi di One Million Coins (`MASTER` è quella del `Failed:` vero) | tutti gli esiti: `rifiutata`, `rinviata`, `rilassata`, `errore`, `interrotta`, `ok` dal traduttore e dalla memoria, una frase rifiutata e poi tradotta, l'ultima riga scritta a metà (gioco chiuso di colpo) |

`test/prova_controllo.py` usa questi file per il controllo dopo aver giocato: li copia nelle cartelle di prova al posto
di quelle vere (`%TEMP%`, `LocalLow`, la cartella del gioco), cambiando dove serve il percorso del gioco scritto dentro.

Il registro del crash e quello della partita riuscita vengono da **due copie** dello stesso gioco, in cartelle
diverse: le cartelle `LocalLow` e `%TEMP%` del gioco sono condivise tra le copie, quindi i segnali vanno
attribuiti con il percorso del gioco scritto nel registro (`Mono path[0]`, `Symbol-SearchPath`).
