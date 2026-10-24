# PatchSmith: dettagli tecnici

*Come funziona PatchSmith motore per motore, tutti i controlli, la riga di comando, le prove e come aggiungere un adapter. Per usarlo basta il [LEGGIMI](../LEGGIMI.md) (in inglese: [README](../README.md)). Questo documento in inglese: [TECHNICAL.md](TECHNICAL.md).*

## Lingua dell'interfaccia

I testi del programma sono scritti in inglese nel codice; ogni altra lingua è un file `patchsmith/ui_language/<codice>.json`, per esempio l'italiano:

```json
{"name": "Italiano", "flag": {"vertical": ["#009246", "#ffffff", "#ce2b37"]}, "texts": {"Create report": "Crea rapporto", "Engine: %s": "Motore: %s"}}
```

- La traduzione avviene quando la finestra mostra un testo: pulsanti, etichette, titoli, menu, finestre di dialogo e righe del registro. Una voce con `%s`, `%d`... vale per tutte le righe di quella forma (`"Translated %d of %d"` traduce "Translated 400 of 1654"), e i pezzi di testo dentro si traducono a loro volta. Quello che non è nel file resta in inglese; il registro salvato su disco è in inglese.
- **Aggiungere una lingua** (es. francese): `python -I smith.py interface --create fr --name Français --flag "{\"verticali\": [\"#0055a4\", \"#ffffff\", \"#ef4135\"]}"`. PatchSmith estrae dal codice tutti i testi per chi lo usa e li traduce con il traduttore locale (i segnaposto `%s`, `%d` sono protetti; le voci già nel file restano, quindi le correzioni a mano non si perdono). Poi conviene rivedere a mano i testi della finestra: la traduzione automatica sbaglia soprattutto le parole isolate dei pulsanti.
- La bandiera è a strisce (`{"vertical": [colori]}` o `{"horizontal": [colori]}`), oppure un'immagine PNG 24x16 accanto al file (`{"file": "fr.png"}`).
- Al primo avvio la lingua è quella di Windows se PatchSmith ce l'ha (oggi l'italiano), altrimenti l'inglese. La lingua scelta con la bandierina viene salvata e usata da lì in poi.

## Il modo più semplice: l'interfaccia grafica

Fai doppio clic su **`PatchSmith.exe`** (oppure su `PatchSmith.pyw`, o lancia `python -I smith.py gui`). `PatchSmith.exe` è un piccolo avvio con l'icona del programma: apre PatchSmith con il Python installato (il più recente dal 3.10 in su) e, se Python manca, spiega cosa installare. Lo compila PatchSmith stesso con il compilatore C# che c'è in ogni Windows (sorgente: `patchsmith/launcher.cs`), senza scaricare nulla: se manca, compare alla prima apertura di `PatchSmith.pyw`. Si può fissare a Start o copiare sul Desktop come collegamento.

L'icona della finestra (un martello sopra una cartella, quella del gioco) è disegnata da `patchsmith/icon.py`, senza file scaricati: `python -I patchsmith/icon.py` rifà le immagini in `patchsmith/icon/` (PNG di ogni lato e `PatchSmith.ico`) e `PatchSmith.exe`.

1. **Sfoglia…**: scegli la cartella del gioco (quella che contiene l'eseguibile). La casella si riempie solo così: non si può scrivere a mano, quindi niente percorsi sbagliati.
2. Le lingue di solito non vanno toccate:
   - **Da** è "Rileva automaticamente": il programma riconosce la lingua del gioco dai suoi testi, senza rete. Guarda l'alfabeto (giapponese, cinese, coreano, cirillico…) e, per le lingue con alfabeto latino, le parole più frequenti.
   - **A** parte dalla lingua di Windows (per te: Italiano).

   I menu mostrano i nomi, per esempio "Inglese (en)", e si sceglie solo dall'elenco. Una lingua salvata che non c'è più nell'elenco torna a quella predefinita.
3. **Traduttore:** scegli quale usare (vedi "Traduttori" più sotto). Il predefinito è il **traduttore locale**, anche su un PC dove non è ancora installato: accanto c'è "da installare" con il pulsante **Installa…**, che elenca cosa scarica e parte solo dopo il tuo sì. Accanto scegli il modello: "Veloce (4B)" o "Qualità (12B, più lento)". Senza una scheda video adatta (grafica integrata nel processore, schede vecchie o con meno di 3,5 GB di memoria) il traduttore locale funziona lo stesso ma usa il processore, anche 10-20 volte più lento: la finestra di installazione lo dice prima di scaricare (e avvisa anche con meno di 8 GB di RAM), proponendo DeepL come alternativa. Se non ne hai ancora scaricato nessuno, PatchSmith propone quello adatto alla tua scheda video: il 12B con almeno 8 GB di memoria video, altrimenti il 4B (la memoria si legge con `nvidia-smi` del driver NVIDIA, dal registro di Windows o da WMI; non si scarica nulla). Dopo l'installazione traduce una frase di prova ("Start game") e la riga in basso dice se funziona, se usa la scheda video e quanto ci mette (se la prova non riesce, una finestra lo spiega); se usa solo il processore, accanto al traduttore compare "lento: senza scheda video" (installato e veloce: nessuna scritta). Google gratuito è indicato come "non ufficiale" e, quando lo scegli, una finestra spiega i blocchi. Per DeepL incolla la tua chiave; per MyMemory puoi indicare la tua email. Accanto compare se il servizio è in pausa dopo un blocco.
4. Premi **Analizza e traduci**. Prima di cominciare PatchSmith fa dei controlli veloci (vedi "Prima di partire") e il pulsante **Ferma** interrompe al primo punto sicuro: le traduzioni fatte restano salvate.
5. Avvia il gioco.

Gli altri pulsanti:

| Pulsante | A cosa serve |
|---|---|
| **Correggi traduzioni…** | Apre la finestra con tutte le traduzioni del gioco, la ricerca e gli elenchi dei testi da guardare (rimasti in originale, troppo lunghi, sospetti…). Correggi una riga e premi "Salva e applica al gioco": PatchSmith la scrive nel gioco da solo (vedi "Correggere le traduzioni") |
| **Ripristina originali** | Rimette i file originali, identici byte per byte. Le traduzioni restano salvate: "Analizza e traduci" le rimette subito, senza tradurle di nuovo (traduce solo i testi nuovi, se un aggiornamento del gioco ne ha aggiunti) |
| **Crea rapporto** (in basso a destra) | Crea sul Desktop uno zip da inviare a chi ti aiuta, senza la chiave DeepL e senza l'email (vedi sotto) |

**Alla fine di ogni operazione** una finestra dice com'è andata, con i pulsanti per il passo successivo:

| Esito | Cosa dice la finestra | Pulsanti |
|---|---|---|
| Tradotto | Come vedere la traduzione nel gioco (per Unity: ALT+T e chi traduce le frasi nuove mentre giochi; se adesso non c'è nessun traduttore lo spiega), quanti testi sono tradotti | OK, Installa il traduttore locale (se le frasi nuove resterebbero in originale) |
| Tradotto in parte | Quanti testi sono rimasti in originale e perché; cosa ha già riprovato PatchSmith da solo (vedi "Testi scartati e traduzioni da rifare") e le correzioni a mano che il gioco non sa mostrare | Mostra i rimasti, Correggi traduzioni (con l'elenco giusto già scelto), Usa/Installa il traduttore locale |
| Traduzione interrotta | Il traduttore si è fermato (per esempio Google in pausa dopo un blocco): le traduzioni fatte restano | Usa/Installa il traduttore locale, Mostra i rimasti |
| Il gioco è aperto | Niente è stato toccato, oppure le traduzioni sono pronte ma non ancora scritte | Riprova, Riapplica |
| Gioco non ancora supportato | Il motivo in parole semplici (per esempio Unity IL2CPP senza tabelle dei testi, tabelle senza la lingua di partenza, un catalogo binario o un'impronta dei file che non si sa togliere) e che non è stato modificato nulla | Crea rapporto |
| Lingua ufficiale | Il gioco ha già l'italiano: sceglilo nel suo menu | Traduci comunque (dove si può) |
| Anti-cheat, traduttore non pronto, file bloccati, cartella sbagliata, niente da fare, già originale | Il motivo e cosa fare | Installa il traduttore locale, Riprova, Crea rapporto (dove servono) |
| Errore imprevisto | Il tipo di errore; i dettagli tecnici sono nel registro salvato | Crea rapporto |

**Riprova** ripete l'ultima operazione sul gioco con le stesse scelte; dopo l'installazione del traduttore locale, un rapporto o un errore in un comando della finestra non c'è (non c'entrano con il gioco). **Usa il traduttore locale** compare se è già installato e non è quello scelto: lo sceglie e ripete "Analizza e traduci" con le stesse scelte (anche dopo "Traduci comunque"); se non è installato, il pulsante è **Installa il traduttore locale**.

Se il motore non è ancora supportato ma ci sono file con testo, propone una traduzione **sperimentale**.

### Prima di partire: i controlli veloci

Prima di "Analizza e traduci", "Ripristina originali" e "Riapplica" (che propongono le finestre di fine e il controllo dopo aver giocato: riscrive le traduzioni salvate senza rete e senza tradurre testi nuovi) PatchSmith controlla in un attimo quello che altrimenti scopriresti solo dopo minuti (o ore) di lavoro. Per ogni problema trovato una finestra spiega cosa succede, e l'operazione parte solo se scegli di continuare:

| Cosa controlla | Quando | La finestra | Pulsanti |
|---|---|---|---|
| Il gioco è aperto | sempre | Chiudilo e premi "Riprova" | Riprova (di nuovo i controlli), Annulla |
| Le lingue Da/A | Analizza e traduci | Lingua non riconosciuta, o Da e A uguali: scegli dall'elenco. Un codice fuori elenco si può usare | OK, oppure Continua comunque / Annulla |
| Si può scrivere nella cartella del gioco (un file di prova, tolto subito) | sempre | Windows nega l'accesso: con il gioco in "Programmi" spiega come spostarlo (Steam: Sposta la cartella di installazione) o di riaprire PatchSmith come amministratore; altrimenti l'"Accesso controllato alle cartelle" o l'antivirus. Non tocca nulla | OK |
| Spazio libero: almeno 500 MB sul disco del gioco e 100 MB su quello dei dati di PatchSmith | Analizza e traduci, Riapplica | Con il disco pieno l'operazione si fermerebbe a metà | Annulla, Continua comunque |
| Il traduttore online scelto è in pausa dopo un blocco | Analizza e traduci | Fino a quando, e che il traduttore locale non ha blocchi | Usa il traduttore locale (o Installa il traduttore locale), Continua comunque (solo le traduzioni già salvate), Annulla |
| Il traduttore locale è scelto ma non installato | Analizza e traduci | Si scarica una volta sola. Senza installarlo il gioco si analizza lo stesso: se ha già la sua traduzione ufficiale basta quella, altrimenti ci si ferma prima di modificarlo | Installa ora, Continua comunque, Annulla |
| Lo stesso gioco è già tradotto in un'altra cartella (vedi sotto) | Analizza e traduci, cartella senza traduzioni | Di quale gioco si tratta e quanti testi ha salvati | Sì, riusale; No, traduci da capo; Annulla |

"Usa il traduttore locale" lo sceglie e riparte; "Installa" lo installa (con la frase di prova) e poi riparte da solo, se la frase di prova riesce (se no una finestra dice perché). Mentre una di queste finestre è aperta il controllo dopo aver giocato all'apertura aspetta: l'operazione che scegli parte comunque.

**Lo stesso gioco in un'altra cartella.** Se copi, sposti o reinstalli un gioco già tradotto, la cartella nuova non ha traduzioni: PatchSmith riconosce lo stesso gioco (stesso prodotto di Unity, stesso gioco di Steam, un eseguibile con lo stesso nome, o in mancanza lo stesso nome di cartella) e propone di **riusare le sue traduzioni**, nella stessa lingua. Se la cartella di prima c'è ancora (una copia), il profilo nuovo parte con una copia della memoria (anche le tue correzioni) e dei nomi da non tradurre: si traducono solo i testi che mancano. Se non c'è più (gioco spostato), il profilo di prima passa alla cartella nuova. Per riconoscere un gioco spostato il profilo ricorda i suoi dati (`identity`); i profili delle versioni precedenti si riconoscono dalla loro cartella, se c'è ancora, o dal nome.

### Correggere le traduzioni

**Correggi traduzioni…** apre una finestra con tutte le traduzioni del gioco scelto: a sinistra il testo originale, poi la traduzione e il problema trovato; per i giochi che fanno conoscere lo spazio dei loro testi (GameMaker con gli a capo) anche la colonna "Limite": riga più lunga della traduzione / caratteri che ci stanno nel gioco. Per Unity la traduzione è quella che il gioco mostra davvero (la cache di XUnity), comprese le frasi tradotte mentre giocavi.

- **Cerca** trova un testo nell'originale o nella traduzione (senza badare alle maiuscole).
- **Mostra** sceglie l'elenco, con quanti testi ha:

  | Elenco | Cosa contiene |
  |---|---|
  | Rimasti in originale | testi senza traduzione (anche quelli che il traduttore ha scartato) e traduzioni rimaste uguali all'inglese; per Unity le righe della cache come `Open the door=Open the door` |
  | Troppo lunghi | testi che escono dal loro spazio: nei giochi che lo fanno conoscere (GameMaker con i file di lingua) la riga più lunga delle versioni del gioco, misurata come la scrive il gioco; negli altri le etichette corte (fino a 3 parole) diventate lunghe almeno il doppio dell'originale; e le etichette che PatchSmith ha già chiesto più corte senza riuscirci (vedi "Lettere che i font non hanno ed etichette troppo lunghe") |
  | Non scritte nel gioco | traduzioni che il gioco non saprebbe mostrare (caratteri che i suoi font non hanno, forma diversa dall'originale), con il motivo |
  | Con numeri o segnaposto | i testi con numeri o codici come `{0}`: prima quelli in cui non tornano |
  | In un'altra lingua | traduzioni in un'altra lingua, o copiate da una traduzione ufficiale di un'altra lingua |
  | Sospetti | traduzioni salvate che non passano i controlli, frasi tradotte mentre giocavi senza tutti i controlli, una parola diventata una frase, un pulsante con il verbo all'infinito ("Esportare" invece di "Esporta") |
  | Corrette a mano | le tue correzioni |

- Scegli una riga e correggi la traduzione nella casella sotto. Mentre scrivi PatchSmith dice la lunghezza (e quanto spazio c'è nel gioco) e avvisa se manca un segnaposto, se ce n'è uno in più, se i numeri o le righe non sono quelli dell'originale. Sotto c'è anche il problema della riga per intero.
- **Salva e applica al gioco** salva le correzioni (tutte insieme) e chiude la finestra; poi PatchSmith le scrive nel gioco da solo, come "Riapplica" (prima i controlli: se il gioco è aperto, chiudilo e premi "Riprova"). Una traduzione vuota non si salva; con un segnaposto che non torna PatchSmith chiede se salvare lo stesso. Chiudendo la finestra con correzioni non salvate, chiede se salvarle.
- Le correzioni vanno nella memoria del gioco con lo stesso lucchetto del traduttore dal vivo (il file resta sempre valido), in `.automatiche.json` resta la traduzione automatica (così si riconoscono e PatchSmith non le tocca più; per un testo che lì non c'era, quella di prima, o una voce vuota se era rimasto in originale), escono dall'elenco dei rimasti, e i testi brevi passano nella memoria comune di tutti i giochi. Per Unity la riga della cache di XUnity si sostituisce solo a gioco chiuso: finché non è scritta resta in `memory/<gioco>.<lingua>.da_scrivere.json`, e la scrive "Riprova" (a gioco chiuso), la prossima "Riapplica" o "Analizza e traduci" oppure, se non hai premuto niente, il controllo dopo aver giocato alla prossima apertura di PatchSmith. Se nel frattempo quella riga è stata cambiata a mano nel file, resta com'è.
- **Ritraduci selezionati…** ritraduce le righe scelte (Maiusc o Ctrl per sceglierne più di una) con domande nuove ogni volta che lo premi, così la traduzione può venire diversa, anche per le righe corrette a mano. Con il traduttore locale scegli il modello (Veloce o Qualità, tra quelli scaricati). Se la nuova traduzione non passa i controlli resta quella di prima. Poi le scrive nel gioco.
- **Apri il file (avanzato)** apre il file delle traduzioni come prima; se Windows non ha un programma per quel tipo di file, con il Blocco note. Se il file delle traduzioni è rovinato (per esempio modificato a mano con un errore), la finestra non si apre e lo dice, proponendo di aprire il file.
- **Tastiera:** Ctrl+F cerca; Invio (o F2) dall'elenco porta alla traduzione; Ctrl+Invio conferma e passa alla riga dopo; Esc annulla la modifica della riga (dall'elenco chiude la finestra); Ctrl+S salva; Alt+frecce (o Ctrl+PagGiù/PagSu) cambiano pagina; Ctrl+A sceglie tutte le righe della pagina. L'elenco mostra 300 righe per pagina, così resta veloce anche con decine di migliaia di testi.

Nelle finestre di fine operazione, con testi rimasti in originale, traduzioni che il gioco non sa mostrare o etichette troppo lunghe, c'è il pulsante **Correggi traduzioni**, che apre la finestra già sull'elenco giusto; c'è anche in "Mostra i rimasti".

### Lettere che i font non hanno ed etichette troppo lunghe

Due cose che prima si vedevano solo giocando, adesso PatchSmith le controlla da solo, per tutti i motori.

**Lettere che i font del gioco non sanno disegnare.** Al loro posto il gioco mostrerebbe un quadratino (o niente, o in XNA si chiuderebbe). PatchSmith non blocca la traduzione: sostituisce solo quelle lettere con la più simile che il font ha (`è` diventa `e'`, `ò` diventa `ó` se il font ce l'ha, altrimenti `o'`; virgolette e trattini tipografici diventano quelli semplici). Una traduzione che non si può sistemare così (per esempio con un'emoji) resta in originale, va nell'elenco "Non scritte nel gioco" e, se l'ha scritta PatchSmith, alla prossima traduzione la rifà senza quei caratteri. In memoria la traduzione resta quella vera: l'adattamento vale solo per quel gioco.

| Motore | Come sa quali lettere hanno i font |
|---|---|
| GameMaker, XNA/MonoGame | legge i font del gioco (come prima) |
| Unity Localization (tabelle dei testi) | legge i font TextMeshPro del gioco, nei bundle e nei file di `_Data`. Una lettera va bene se almeno un font la sa disegnare (TextMeshPro cerca quelle che mancano negli altri font); un font "dinamico" vale con tutte le lettere del suo file `.ttf`. Se non si capisce (nessun font TextMeshPro, font dinamici senza il loro `.ttf`, font di Windows) non limita nulla. La prima volta su un gioco grande può richiedere fino a qualche minuto (SWORN: 8 secondi); poi il risultato resta in `memory/<gioco>.unity/font.json` finché i file del gioco non cambiano |
| Unity con XUnity | durante il gioco: PatchSmith accende nel registro di BepInEx (`BepInEx.cfg`, `[Logging.Disk] WriteUnityLog = true`) i messaggi di Unity, dove TextMeshPro scrive ogni lettera che non trova (*"The character with Unicode value è was not found in the [...] font asset"*). Il controllo dopo aver giocato le legge (anche dal `Player.log`) e le sistema (vedi la tabella sotto) |
| Unreal | le lettere delle traduzioni ufficiali del gioco in lingue latine (francese, tedesco, spagnolo...): se le usano, il font ha tutte le lettere accentate comuni (anche `ì` e `ò`). Senza traduzioni ufficiali con lettere accentate non limita nulla |
| Web | nessun limite: il browser usa i font di Windows |

**Etichette troppo lunghe.** Dopo la traduzione PatchSmith guarda le etichette corte (fino a 3 parole: pulsanti, voci dei menu) che ha tradotto in automatico. Una è troppo lunga se supera lo spazio che ha di solito:
- se il gioco ha traduzioni ufficiali di quel testo (o, in GameMaker, la larghezza delle sue righe): più di 1,3 volte l'originale più 2 caratteri e più della traduzione ufficiale più lunga;
- se no: almeno il doppio dell'originale e 7 caratteri in più (`Credits` → `Ringraziamenti`, `Slider` → `Dispositivo di scorrimento`). Sulle memorie dei giochi già tradotti sono il 3-8% delle etichette; la sola regola dell'1,3 ne prendeva il 15-30%, anche le normali `Statistiche` e `Combattimento`.

Con il **traduttore locale** le etichette troppo lunghe si chiedono di nuovo, una volta sola, dicendo il numero massimo di caratteri: resta la traduzione più corta che passa i controlli (`Credits` → `Crediti`). Quelle che restano troppo lunghe, e con gli altri traduttori (che non sanno di uno spazio massimo) tutte, vanno nell'elenco **"Troppo lunghi"** di "Correggi traduzioni", con il testo originale, e la finestra di fine lo dice. Le etichette annotate con un traduttore online si chiedono più corte (una volta) alla prima traduzione con il traduttore locale; con il traduttore locale anche "Ritraduci" su una voce dell'elenco "Troppo lunghi" la chiede con il suo spazio. Le tue correzioni a mano non si toccano mai.

### Dopo aver giocato: il controllo automatico

Dopo una partita il gioco lascia tracce su disco: i registri di Unity e di BepInEx, le cartelle dei crash, il diario del traduttore delle frasi nuove, i file che un aggiornamento ha rimesso originali. **Quando apri PatchSmith** il programma guarda (solo le date dei file, in un attimo) quali giochi tradotti hanno tracce nuove dall'ultimo controllo e li controlla da solo, in background: il risultato compare nel registro della finestra. Solo se c'è qualcosa da fare, in fondo alla finestra (a sinistra di "Crea rapporto") compaiono i pulsanti **Dettagli ultima partita** e **Ritraduci N frasi**. Il controllo all'apertura fa da solo solo le cose veloci (crash, configurazione, traduttore delle frasi nuove, file rimessi dal gioco); le frasi del diario da ritradurre le lascia al pulsante **Ritraduci N frasi** (o a "Ritraduci le frasi" nei Dettagli): ritradurle vorrebbe dire caricare il traduttore locale o aspettare le pause di Google con i pulsanti fermi. Se nel frattempo hai già avviato un'altra operazione, il controllo parte da solo appena quella finisce (il registro lo dice). Dalle finestre dei **Dettagli** i pulsanti ("Ripristina originali", "Riapplica", "Crea rapporto"...) valgono per il gioco di quella finestra: la cartella scelta diventa la sua. Dalla riga di comando: `python -I smith.py check <cartella>` (senza cartella: tutti i giochi); con `--dry-run` dice cosa farebbe senza toccare nulla.

Contano solo le tracce arrivate **dopo l'ultima volta che PatchSmith ha scritto nel gioco** (installazione, Riapplica, una correzione): un crash di prima della correzione non conta più. Le cartelle dei registri e dei crash di Unity sono le stesse per tutte le copie di un gioco: un registro conta solo se è della copia giusta (il percorso del gioco scritto dentro: `Mono path[0]`, o `Discovering subsystems at path` nei giochi IL2CPP). Dei registri molto grandi (oltre 2 MB) si leggono l'inizio e la fine. Dopo un aggiornamento di PatchSmith i giochi Unity si ricontrollano una volta all'apertura, per portarli alla configurazione della versione nuova.

| Cosa trova | Cosa fa da solo |
|---|---|
| Unity: il gioco si chiude all'avvio mentre parte BepInEx (crash dentro `Application:.cctor`, oppure `LogOutput.log` senza "startup complete") | BepInEx parte più tardi, al primo MonoBehaviour, con qualsiasi versione di Unity. Se il gioco si chiude **anche dopo**, con un crash vero (una cartella dei crash di Unity con BepInEx nella pila, o un crash nel registro di Windows), propone "Ripristina originali" (non lo fa da solo): probabilmente il gioco non accetta mod. Un `LogOutput.log` incompleto senza un crash (per esempio il gioco chiuso mentre caricava, con BepInEx che parte già al primo MonoBehaviour come nei giochi Unity 6) è solo da sapere |
| Unity: il traduttore delle frasi nuove si è fermato (XUnity con Google che dà errori, "Failed:", controllo anti-spam, troppi errori di fila) | Rimette il traduttore di PatchSmith nel gioco. Se era già quello di PatchSmith, le frasi rimaste si ritraducono dal diario; senza traduttori disponibili propone "Installa il traduttore locale" |
| Unity: le frasi nuove le chiede XUnity a Google (o nessuno), per esempio in un gioco tradotto con una versione vecchia; Google magari è in pausa | Le passa al traduttore di PatchSmith. Se adesso non c'è nessun traduttore disponibile, propone "Installa il traduttore locale" |
| Unity: installato con una versione precedente di PatchSmith, configurazione di XUnity o di BepInEx cambiata (per esempio un'opzione di prova di XUnity accesa, o i messaggi di Unity non scritti nel registro di BepInEx) | Rimette la configurazione di questa versione e scrive la versione nel manifesto |
| Unity: il traduttore di PatchSmith non può partire (Python o PatchSmith spostati, il plugin scrive che è mancato qualcosa) | Rifà il plugin e il suo comando |
| Unity: l'installazione della traduzione si è interrotta a metà | Niente da solo: propone "Ripristina originali" e poi "Analizza e traduci" |
| Unity: mancano file installati (per esempio tolti dall'antivirus) | Li rimette dai componenti già scaricati; se tornano a mancare propone "Riapplica" e un rapporto |
| Unity (anche Unity Localization, dal `Player.log`): lettere delle traduzioni che i font del gioco non sanno disegnare (avvisi di TextMeshPro) | Le annota nel profilo del gioco (`missing_chars`): da allora ogni traduzione scritta nel gioco, anche dal traduttore delle frasi nuove, le sostituisce (`è` → `e'`, solo con lettere semplici). Sistema subito quelle già scritte: con XUnity le righe della cache scritte da PatchSmith (quelle cambiate a mano restano, e te lo dice), con Unity Localization con "Riapplica". Le lettere che ci sono solo nei testi del gioco, non nelle traduzioni, non contano |
| Unity: frasi del diario rimaste in originale o tradotte senza tutti i controlli mentre giocavi, frasi tradotte ma non salvate | Le frasi perse tornano subito nella memoria. Le altre all'apertura restano da fare, con il pulsante **Ritraduci N frasi**; con quel pulsante o dalla riga di comando le ritraduce con il traduttore scelto e le mette nella cache di XUnity (una riga provvisoria si sostituisce solo se è ancora quella del diario: le tue correzioni restano). Le riprova anche la prossima "Analizza e traduci". Se il traduttore scelto è quello locale e non è installato, la finestra propone "Installa il traduttore locale" |
| Unity: correzioni fatte in "Correggi traduzioni" con il gioco aperto e non ancora scritte (non hai premuto "Riprova") | Le scrive nella cache di XUnity, senza reinstallare nulla. Una riga cambiata a mano nel file dopo la correzione resta com'è |
| File temporanei rimasti nella memoria da un salvataggio interrotto | Li toglie |
| Giochi con file tradotti: il gioco ha rimesso i suoi file originali (aggiornamento, verifica dei file di Steam), o ha tolto la lingua aggiunta; Unity Localization: un aggiornamento ha rimesso il controllo dell'impronta nel catalogo dei bundle | "Riapplica" da solo (per GameMaker rimette anche la lingua aggiunta nelle opzioni: l'aveva cambiata il gioco) |
| Giochi con file tradotti, Unreal, XNA: il gioco è stato aggiornato con testi nuovi | Propone "Analizza e traduci" |
| GameMaker: nelle opzioni del gioco è scelta un'altra lingua | Te lo dice e basta: potrebbe essere una tua scelta |
| Unreal: il registro del gioco dice che il pak della traduzione non è stato caricato, o che è scelta un'altra lingua | Te lo dice, con il rapporto o la lingua da scegliere |
| Altri motori: il gioco si è chiuso all'improvviso (registro Applicazione di Windows, eventi 1000 e 1026) | Propone "Ripristina originali", che chiede conferma |

Le regole delle correzioni automatiche:
- **solo a gioco chiuso**: se il gioco è aperto non si tocca nulla e non si scrive nulla, nemmeno il risultato; si riprova al prossimo controllo;
- **una volta sola per traccia**: ogni tentativo resta in `profiles/<gioco>.controllo.json` con la data e com'è andato; se il problema torna, resta "da fare" con il pulsante giusto e il rapporto;
- **reversibili**: toccano solo i file di PatchSmith nel gioco (configurazione di BepInEx e XUnity, plugin, cache delle traduzioni) e la memoria, o rimettono le traduzioni salvate; "Ripristina originali" toglie tutto come sempre;
- **mai i salvataggi del gioco**;
- tutto finisce nel registro (`logs/...-controllo.log` per il controllo all'apertura) e nella finestra **Dettagli**.

Se PatchSmith viene aggiornato mentre la sua finestra è aperta, alla prossima operazione un avviso dice di chiuderla e riaprirla (altrimenti si userebbe ancora il codice di prima).

**Registri e rapporto.** Il registro di ogni operazione si salva anche su disco, in `logs/` (un file per operazione, `AAAAMMGG-HHMMSS-<gioco>.log`; restano gli ultimi 30), con i dettagli tecnici degli errori imprevisti che nella finestra non compaiono. **Crea rapporto** mette in uno zip sul Desktop:
- i registri delle ultime 5 operazioni sul gioco e l'ultimo di ognuno dei registri generali (errori della finestra, installazione del traduttore locale, controllo all'apertura), il profilo, le impostazioni **senza la chiave DeepL e senza l'email**, lo stato dei traduttori;
- il riconoscimento del motore, i manifesti di PatchSmith nel gioco, l'elenco dei file del gioco modificati e la versione di PatchSmith;
- l'elenco dei testi rimasti in originale con il motivo (`memory/<gioco>.<lingua>.problemi.json`), le correzioni non ancora scritte nel gioco (Unity: `<gioco>.<lingua>.da_scrivere.json`) e le lettere che i font del gioco sanno disegnare (Unity Localization: `font.json`);
- il risultato del controllo dopo aver giocato (`profiles/<gioco>.controllo.json`: tracce viste, correzioni provate e com'è andata);
- per Unity: `BepInEx/LogOutput.log`, la configurazione di XUnity e di BepInEx, lo stato dell'avvio del traduttore (`BepInEx/patchsmith-servizio-stato.txt`), la fine del registro e del diario delle frasi del traduttore dal vivo, `Player.log`, `Player-prev.log` e il `Player.log` dell'ultimo crash.
- per Unreal: il registro del gioco (`%LOCALAPPDATA%\<progetto>\Saved\Logs\<progetto>.log`), da cui il controllo dopo aver giocato capisce se il pak della traduzione è stato caricato e quale lingua è scelta.

Niente file del gioco e niente salvataggi. Se la chiave o l'email fossero finite in un registro (anche dentro un indirizzo web), nel rapporto diventano `<removed>`; la cartella del tuo utente di Windows diventa `%USERPROFILE%`. Se sul Desktop non si può scrivere (per esempio con l'"Accesso controllato alle cartelle" di Windows), il rapporto va nella cartella `logs/` e la finestra dice dove.

## Da riga di comando

```bash
python -I smith.py auto "D:\giochi\MioGioco"
```

Questo comando fa tutto in automatico: rileva il motore, trova i testi, traduce e applica. Funzionano ancora anche i nomi italiani di prima di comandi e opzioni (`traduci`, `--forza`...).

| Comando | A cosa serve |
|---|---|
| `python -I smith.py detect <cartella>` | Riconosce il motore e la strada consigliata |
| `python -I smith.py scan <cartella> --name mio-gioco` | Crea il profilo da controllare a mano |
| `python -I smith.py translate mio-gioco` | Traduce i testi mancanti e li applica |
| `python -I smith.py apply mio-gioco` | Riapplica dalla memoria, senza rete |
| `python -I smith.py restore mio-gioco` | Rimette gli originali; accetta anche la cartella del gioco, e rimette pure la lingua impostata da PatchSmith |
| `python -I smith.py status mio-gioco` | Quanti testi sono tradotti, con esempi |
| `python -I smith.py package` | Prepara il pacchetto da pubblicare (vedi "Pacchetto da pubblicare") |
| `python -I smith.py check <cartella>` | Controllo dopo aver giocato (vedi sopra); senza cartella tutti i giochi, `--dry-run` non corregge nulla. Esce con 0 se non resta niente da fare, 2 se resta qualcosa (anche una correzione rimandata perché il gioco è aperto, o che con `--dry-run` non è stata fatta), 1 per un errore |

Opzioni utili:
- `--from` e `--to`: le lingue. `--from` è predefinito su `auto` (lingua rilevata dai testi), `--to` sulla lingua di Windows. Accettano codici (`en`) o nomi (`inglese`);
- `--backend locale` / `deepl` / `mymemory` / `google`: traduttore da usare. Se non lo indichi: quello locale; se non è installato, l'errore dice il comando per installarlo. La chiave DeepL, l'email per MyMemory e il modello locale si leggono da `settings.json` (le salva l'interfaccia) o dalle variabili `PATCHSMITH_DEEPL` / `PATCHSMITH_EMAIL` / `PATCHSMITH_MODEL` (`4b` o `12b`);
- `python -I smith.py local`: stato del traduttore locale e, se non è installato, il modello consigliato per la tua scheda video; `--install 12b` (oppure `4b`, o entrambi) lo scarica e installa, poi traduce una frase di prova (esce con 2 se la prova non riesce);
- `python -I smith.py services`: stato dei traduttori (pause dopo un blocco); `--reset google` lo riattiva (per un traduttore disattivato lo ricorda anche l'elenco);
- `--backend fake`: prova tecnica senza rete, antepone `[it]` ai testi. Disponibile solo da riga di comando, non nell'interfaccia grafica;
- `--force` (per `auto`): prova anche con i motori non supportati;
- `--regenerate`: ricrea il profilo da zero.

I messaggi parlano dei pulsanti dell'interfaccia; quando c'è un comando per rimediare (per esempio installare il traduttore locale), la riga di comando lo scrive sotto l'errore. I codici di uscita restano quelli di sempre: 0 fatto (anche quando il gioco ha già la lingua ufficiale), 2 fatto in parte, niente da fare o non possibile, 1 errore.

**Dove stanno i tuoi dati.** Profili (`profiles/`), memorie delle traduzioni (`memory/`, con le traduzioni automatiche, i testi con problemi, la provenienza della memoria comune, le correzioni da scrivere nella cache di XUnity), impostazioni (`settings.json`), stato dei traduttori (`services.json`), lingue impostate (`languages/`, `configurations/`), copie dei salvataggi (`save-backups/`), registri delle operazioni fatte dall'interfaccia (`logs/`) e il risultato del controllo dopo aver giocato (`profiles/<gioco>.controllo.json`) stanno accanto al programma. Con la variabile d'ambiente `PATCHSMITH_DATA` vanno in un'altra cartella; il programma e i componenti scaricati (`components/`) restano dove sono. Le prove automatiche la usano per non toccare mai i dati veri. Il traduttore dal vivo dei giochi Unity lo avvia il gioco, che di norma non ha la variabile: usa i dati accanto al programma. Le versioni di sviluppo prima della 0.1.0 usavano nomi italiani (`profili`, `memoria`, `registri`, `salvataggi`, `configurazioni`, `lingue`, `componenti`, `impostazioni.json`, `stato_servizi.json`): all'avvio PatchSmith li sposta da solo sui nomi nuovi, senza copiare né cancellare nulla (una cartella in uso resta dov'è e si usa finché non si può spostare), e un gioco tradotto prima che chiede il profilo con il percorso vecchio lo ritrova.

## Come funziona

1. **Rilevamento.** Usa le regole di SteamDB sui nomi dei file (circa 125 motori, licenza MIT), controlla i primi byte dei file (`.pck` Godot, `data.win` GameMaker, `.xp3` KiriKiri, `.rpa` Ren'Py, `UnityFS`...) e riconosce i framework web dentro Electron/NW.js. Ogni motore riceve un punteggio con le evidenze. Segnala anche gli anti-cheat.
2. **Testi nei dati (adapter `web-json`).** Cerca i file JSON con testo leggibile e sceglie quali campi tradurre. Esclude automaticamente:
   - chiavi tecniche come `id`, `url`, `path`, `author`, `...Id`, `...Type`;
   - valori che non sono testo: nomi di file, colori, identificatori come `room_12` o `UnlockFirstNewRule`, righe di codice, valori di 1–2 caratteri.

   In più:
   - **JSON con commenti** (`//` e `/* */`) e con la virgola prima di `}` o `]`: molti giochi li accettano, e PatchSmith li legge. Li riscrive cambiando solo le frasi tradotte: commenti, spazi, a capo e numeri restano identici byte per byte;
   - **file di lingua** `{chiave: testo}`, anche divisi in sezioni (`"menu": {"_play": "Play"}`): un file così, o un file presente in più lingue (`lang/en.json`, `lang/fr.json`, `stringsEN.json`, `stringsFR.json`), si traduce tutto, **anche le parole sole** (`wood`, `PLAY`, `quit`). Restano fuori gli identificativi (`icons/wood.png`, `menu_title_2`). Non sono file di lingua quelli con un elenco di oggetti (`[{"id": "sword", "name": "Sword"}]`: sono dati del gioco, come `PluginInfo.json` dei suoni, e lì si traducono solo le frasi con le regole di sempre) né, per i file senza la lingua nel nome, i salvataggi e i file di opzioni;
   - **tabelle CSV con una colonna per lingua** (`translations.csv` con `id,english,french,...`): si traduce la colonna della lingua originale (vedi "Giochi GameMaker"). Se la colonna italiana è già piena, è la traduzione ufficiale;
   - nei giochi **GameMaker** e **XNA/MonoGame** che arrivano qui (nessun file di lingua riconosciuto: traduzione sperimentale), il profilo ricorda il motore e le traduzioni si adattano ai caratteri che i font del gioco sanno disegnare, come negli altri casi;
   - nel registro: i file saltati, con quanti sono (`.json` non validi o troppo grandi, file con pochi testi lasciati fuori, file del gioco web troppo grandi per i menu).
3. **Menu dei giochi web (adapter `web-ui`).** I testi dei menu sono dentro il codice JavaScript. **Il codice del gioco non viene modificato.** Lo strumento invece:
   - estrae le stringhe con un piccolo lettore di JavaScript;
   - le traduce;
   - genera `patchsmith-traduzione.js` (il dizionario più l'iniettore) e aggiunge una riga `<script>` a `index.html`.

   Mentre giochi, l'iniettore sostituisce solo i testi **visualizzati** che corrispondono esattamente a una voce del dizionario: testi a schermo, tooltip, segnaposto, titolo della finestra, testo disegnato su canvas. Una stringa estratta per errore non fa danni, perché non appare mai a schermo.

   Anche i **pulsanti di una parola sola** (`PLAY`, `OK`, `quit`) entrano nel dizionario, quando di sicuro si vedono: il testo e gli attributi visibili dell'HTML e, nel JavaScript, le stringhe date a `textContent`, `innerText`, `title`…, disegnate con `fillText` o `add.text`, o scritte come `label:` o `text:`. Restano fuori codice, CSS, nomi in camelCase o con il trattino basso, tasti e font.
4. **Lingua del gioco.** Con "Da" automatico viene riconosciuta dai testi estratti:
   - prima dall'alfabeto: kana → giapponese, hangul → coreano, ideogrammi → cinese, poi cirillico, greco, arabo e altri;
   - per l'alfabeto latino, dalle parole più frequenti (inglese, italiano, francese, tedesco, spagnolo, portoghese, olandese, polacco, turco, svedese).

   Se il gioco è già nella lingua di arrivo, il programma non lo traduce.
5. **Traduzione.** Usa il traduttore scelto (il predefinito è il traduttore locale, sul tuo PC; vedi "Traduttori"):
   - con i traduttori online le richieste sono a blocchi (con Google di circa 4.500 caratteri), con pause e nuovi tentativi;
   - gli a capo vengono conservati;
   - segnaposto e codici (`{nome}`, `%d`, `<b>`, `\C[3]`, `**`, i tag di colore e font come `[yellow1]…[/c]`) vengono protetti.
6. **Memoria di traduzione.** Il file `memory/<gioco>.it.json` contiene `{"originale": "traduzione"}`. È modificabile a mano, e le frasi già tradotte non vengono più inviate. Accanto ci sono:
   - `<gioco>.it.automatiche.json`: le traduzioni come le ha scritte PatchSmith. Una voce della memoria diversa da questa è una tua correzione, e PatchSmith non la tocca mai; una voce che qui non c'è (tradotta da una versione che non teneva questo file) conta come automatica;
   - `<gioco>.it.problemi.json`: i testi con un problema, con il motivo (vedi "Testi scartati e traduzioni da rifare"), anche le etichette troppo lunghe per il loro spazio;
   - `<gioco>.it.da_scrivere.json` (solo Unity): le righe della cache di XUnity da cambiare con le correzioni fatte in "Correggi traduzioni", finché il gioco non è chiuso;
   - `<gioco>.unity/font.json` (Unity Localization): le lettere che i font del gioco sanno disegnare, finché i file del gioco non cambiano. Le lettere che il gioco ha detto di non avere stanno invece nel profilo (`"missing_chars"`): togliendole da lì PatchSmith le torna a scrivere.

   Per correggere una traduzione usa "Correggi traduzioni" (vedi "Correggere le traduzioni"): la finestra salva la memoria senza mai rovinarla e scrive le correzioni nel gioco.
   - **Nomi del gioco:** lo studio e il titolo restano come sono ("Cup Dog Games" non diventa "Giochi di cani da coppa"). PatchSmith li legge, senza modificare nulla, da `app.info` dei giochi Unity, dalle informazioni sulla versione degli eseguibili (Proprietà > Dettagli: azienda e prodotto) e dal nome del gioco in Steam; li scrive nel profilo (`"do_not_translate"`, dove puoi aggiungerne altri a mano, o toglierne uno se per esempio vuoi il titolo tradotto: un nome tolto non torna, perché quelli già trovati restano in `"found_names"`) e li protegge come i codici del gioco. Un nome di una sola parola che nei testi compare anche in minuscolo (per esempio "Control") è una parola normale, e si traduce.
7. **Scrittura sicura.**
   - L'originale viene salvato accanto come `*.patchsmith-originale`. La copia viene prima verificata e poi rinominata, quindi non può mai restare un backup a metà.
   - Accanto al backup c'è `*.patchsmith-info`, con l'impronta dell'originale, le impronte di tutto ciò che il programma ha scritto e i file che ha creato. Sta dentro la cartella del gioco, quindi vale per qualsiasi profilo e anche se sposti lo strumento.
   - L'impronta di ciò che sta per scrivere viene registrata **prima** di scrivere.
   - Se un backup risulta alterato, il programma si ferma e non tocca nulla.
   - Prima di scrivere un JSON si controlla che la struttura sia **identica**: cambiano solo le stringhe scelte, e niente `NaN` o `Infinity`. Un JSON con commenti si riscrive sostituendo solo le frasi tradotte, e si rilegge per controllo; lo stesso per le tabelle CSV.
   - Lo script generato viene controllato con Node.js, se è installato.
8. **Aggiornamenti del gioco.** Se un file è stato cambiato da altri (un aggiornamento), il nuovo file diventa l'originale, il precedente viene conservato con un numero di versione e vengono tradotte solo le frasi nuove. Il ripristino non sovrascrive mai un file aggiornato dal gioco.
9. **Ripristino.** Riporta all'originale **tutti** i file modificati nella cartella del gioco, anche senza profilo. Poi elimina backup, informazioni e script.
10. **Prove.** Il traduttore di test (`fake`) usa una memoria separata (`*.prova.json`), quindi non rovina quella vera.

Il programma riconosce lo stesso gioco anche se scegli una cartella superiore o inferiore, e riusa lo stesso profilo.

**Giapponese, cinese e coreano.** I testi in queste lingue hanno regole proprie:
- sono ammesse voci di 1–2 caratteri (設定, はい, 시작);
- sono ammesse frasi lunghe senza spazi e testi con cifre;
- percorsi di file (`img/背景`), nomi di file e codice restano esclusi.

Anche l'iniettore dei menu riconosce le voci di 1–2 caratteri.

## Giochi Unity

**Giochi con Unity Localization (anche IL2CPP).** Molti giochi Unity recenti tengono i testi di ogni lingua in tabelle dentro i bundle di Addressables, per esempio `StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle`. Per i giochi IL2CPP, dove le mod non si possono usare, PatchSmith:
- legge i bundle (UnityFS, compressione LZ4 o LZMA) e i file serializzati al loro interno, seguendo l'albero dei campi;
- traduce i testi della tabella inglese (il campo `m_Localized` di ogni voce), proteggendo codici come `{action.HeavyAttack}` e i tag `<b>`, `<color>`;
- riscrive il bundle senza compressione, che Unity legge lo stesso. Cambia solo quei testi, e lo rilegge per controllare che tutto torni;
- tiene il backup verificato dell'originale e lo rimette con "Ripristina originali".

La lingua di ogni bundle si riconosce anche quando il nome non ha il codice tra parentesi: dal contenuto (il codice della lingua scritto nelle tabelle, per esempio `localization-string-tables-english_assets_all.bundle`, `localization_en_assets_all.bundle` o `chinese(simplified)`) e, se il bundle non si legge, dal nome della lingua. Le tabelle degli asset (immagini e suoni) non si toccano. Se la lingua di partenza ha più bundle, si traducono tutti. Senza le tabelle nella lingua di partenza PatchSmith lo dice, con le lingue che il gioco ha: sceglierne una in "Da:" e riprovare traduce quella.

**Impronta (CRC).** Alcuni giochi scrivono nel loro catalogo (`StreamingAssets/aa/catalog.json`) l'impronta di ogni bundle e rifiutano un bundle cambiato. Allora PatchSmith, prima di riscrivere il bundle, mette 0 all'impronta **solo di quel bundle** nel catalogo: il file resta lungo uguale e il resto è identico. Il catalogo ha il suo backup come gli altri file: "Ripristina originali" lo rimette, e se il gioco lo rimette da solo (aggiornamento, verifica dei file di Steam) il controllo dopo aver giocato riapplica la traduzione. PatchSmith non modifica nulla e lo spiega quando:
- il catalogo è binario (`catalog.bin`, dei giochi Unity più recenti) e il record del bundle non si trova con certezza. Di solito si trova: ogni bundle ha un record con impronta, nome, CRC e dimensione, e si azzerano solo i 4 byte del CRC;
- il catalogo controlla l'impronta ma non nomina il bundle da tradurre.

Prima di tutto controlla l'anti-cheat, anche per questi giochi: con un anti-cheat non modifica nessun file.

Nel gioco si lascia selezionato English. Se il gioco ha già la lingua di arrivo tra le sue tabelle (anche riconosciuta dal contenuto), PatchSmith dice di sceglierla nel menu, anche per i giochi Mono.

**Font.** Prima di riscrivere una tabella PatchSmith guarda quali lettere sanno disegnare i font TextMeshPro del gioco (vedi "Lettere che i font non hanno ed etichette troppo lunghe"): una lettera che nessun font ha diventa la più simile (`è` → `e'`) solo nella tabella del gioco. I font "dinamici" disegnano tutte le lettere del loro file `.ttf`. Su SWORN, Megabonk, Rogue Blight, LumenTale, LEGO Party, MOUSE P.I. e Vampire Crawlers i font hanno tutte le lettere italiane: non cambia nulla. Se dopo una partita il `Player.log` dice che manca una lettera, il controllo dopo aver giocato la annota e riscrive le tabelle senza.

**Giochi Unity Mono.**

Per i giochi Unity (Mono) PatchSmith **non modifica i file del gioco**. Scarica, una sola volta e dalle release ufficiali su GitHub, BepInEx 5 e XUnity.AutoTranslator. Le versioni sono fisse e l'impronta SHA-256 viene verificata; i file restano in `components/` e servono per tutti i giochi.

Poi li installa nella cartella del gioco scelto: `BepInEx/`, `winhttp.dll`, `doorstop_config.ini`, `.doorstop_version`.

- **Pre-traduzione:** prima che tu giochi, PatchSmith legge i testi direttamente dai file del gioco (in sola lettura), li traduce a blocchi e li mette nella cache di XUnity. Così le frasi compaiono **in italiano già dalla prima volta**. Legge, in quest'ordine:
  - le tabelle dei testi di Unity Localization nella lingua del gioco, se ci sono (quelle delle altre lingue no);
  - i file in `<gioco>_Data`, a pezzi (anche quelli da più GB, senza riempire la memoria);
  - il codice del gioco: `Assembly-CSharp` e anche gli altri moduli del gioco (per esempio `SunHaven.Core.dll`, `Mouse.dll`, e `Assembly-UnityScript` dei giochi più vecchi), non le librerie del motore, di .NET e di terzi;
  - i file compressi: `data.unity3d` e i bundle in `StreamingAssets`, senza immagini e suoni, prima quelli che di solito hanno testi. Al massimo 400 MB e un minuto: il registro dice quanti ne ha letti, e i testi rimasti fuori si traducono mentre giochi.
  - Filtri automatici: niente nomi di oggetti (`Paper (17)`), nomi interni (`Level_Grass_0`) o testi in altre lingue già presenti nel gioco.
  - Al massimo 20.000 testi (600.000 caratteri): gli altri si traducono mentre giochi.
- **Interfacce IMGUI:** XUnity di solito non traduce i testi disegnati con IMGUI (`OnGUI`). Se il codice del gioco li usa, in `Assembly-CSharp` o in un altro suo modulo (non nelle librerie di terzi), PatchSmith accende la loro traduzione. Per i giochi già installati lo fa il controllo dopo aver giocato, che rimette a posto la configurazione.
  - I nomi delle lingue nei menu (English, Español, 日本語…) restano invariati.
  - Le traduzioni già presenti nella cache, comprese le tue correzioni, non vengono sovrascritte. L'eccezione sono le righe sbagliate **scritte da PatchSmith**: a ogni "Analizza e traduci" (e "Riapplica") PatchSmith controlla la cache e segnala le righe rimaste uguali all'originale (con almeno due parole frequenti: "Xbox" o "Codec" sono nomi e restano), in un'altra lingua, con righe in più o in meno dell'originale, con un `{{A}}` perso o con un nome del gioco tradotto; in più le risposte provvisorie del traduttore dal vivo (nel diario: `relaxed`, `rejected`, `deferred`). Rifà solo quelle la cui traduzione è proprio quella della memoria, delle traduzioni automatiche o del diario, e le sostituisce solo con una traduzione nuova che non ha lo stesso problema. Una riga cambiata a mano (o scritta da altri, per esempio da XUnity con Google prima di PatchSmith) resta com'è, anche se sembra sbagliata.
- **Traduzione dal vivo:** le frasi non trovate nei file, per esempio quelle composte al momento come "Previous save was 3 mins ago", vengono tradotte mentre giochi. La prima volta compaiono in originale per un attimo, poi tradotte. L'invio a blocchi di XUnity è disattivato perché con Google sbagliava e raddoppiava l'attesa. Nel gioco: **ALT+T** alterna originale e traduzione, **ALT+R** ricarica le traduzioni dopo una correzione.
- **Le frasi nuove le traduce sempre PatchSmith**, qualunque traduttore tu abbia scelto: XUnity le manda al traduttore di PatchSmith sul tuo PC, che usa la memoria del gioco e quella comune, controlla le traduzioni e rispetta le pause dei traduttori online.
  - Esempi: le frasi con i numeri dei giochi con interfaccia IMGUI, come "Hold RMB 0.65s to leap up to 25 m". XUnity toglie i numeri (`{{A}}`) e li rimette dopo; PatchSmith li protegge insieme all'unità attaccata, come `{{A}}s`.
  - **Traduttori in fila:** prima il traduttore locale, se è installato (con il modello Veloce, se c'è: la scheda video serve anche al gioco); poi il traduttore online che hai scelto (DeepL con la tua chiave, MyMemory); poi Google gratuito, se non è in pausa. Se uno non risponde o è in pausa si passa al successivo. Google passa dagli stessi limiti prudenti della traduzione: se dovrebbe aspettare più di un minuto, la frase resta per ora in originale. Dopo un errore di rete (o se DeepL chiede di aspettare) si riprova una volta sola dopo pochi secondi, poi si passa al traduttore successivo: XUnity non aspetta una risposta per minuti.
  - **Mai un errore per una frase difficile:** XUnity smette di chiedere traduzioni dopo 5 errori di fila. Se tutti i traduttori scartano una frase (per esempio perché la traduzione perde un `{{A}}` o è troppo lunga), PatchSmith riprova una volta i pezzi tra i numeri e risponde con la traduzione migliore, purché abbia tutti i `{{A}}` e sia in italiano; altrimenti risponde con l'originale. Queste risposte di ripiego non vanno nella memoria. L'errore arriva a XUnity solo se nessun traduttore è raggiungibile; mentre il gioco si chiude il traduttore non traduce più nulla.
  - **Diario delle frasi:** ogni frase chiesta mentre giochi finisce in `memory/<gioco>.richieste.jsonl`, una riga per frase con testo, esito (`ok`, `relaxed` = risposta di ripiego, `rejected` = lasciata in originale, `deferred`, `error`, `interrupted`), traduttore e motivo. Le frasi non `ok` sono quelle da ritradurre a gioco chiuso. Oltre i 2 MB il diario diventa `.richieste.jsonl.1`.
  - Come funziona: un piccolo plugin (`BepInEx/plugins/Patchsmith/Patchsmith.Servizio.dll`) avvia, all'apertura del gioco e senza finestre, `smith.py xunity-service` (i giochi preparati dalle versioni precedenti lo chiamano con il nome italiano, `servizio-xunity`, che funziona ancora).
    - Il servizio risponde solo su `127.0.0.1`, su una porta fissa per ogni gioco, ed è collegato a XUnity con `Endpoint=CustomTranslate`, senza il controllo anti-spam di XUnity (che spegnerebbe la traduzione quando compaiono molte frasi nuove insieme) e con testi fino a 2500 caratteri.
    - Il plugin viene compilato con `csc` di Windows usando le librerie del gioco stesso; il sorgente è `components/plugin/PatchsmithServizio.cs`.
    - Il comando è scritto in `BepInEx/config/patchsmith-servizio.cfg`. Se il Python indicato non c'è più (spostato o aggiornato), il plugin prova il Python di Windows (`py -3`). Com'è andato l'avvio lo scrive in `BepInEx/patchsmith-servizio-stato.txt`.
  - Avvia Ollama e carica il modello subito, così la prima frase non aspetta.
  - **Si chiude da solo** quando chiudi il gioco e libera la scheda video. Smette subito di ricevere frasi (così, se riapri il gioco, parte il traduttore nuovo); una frase che stava traducendo finisce e si salva prima della chiusura.
  - Registro: `components/ollama/servizio-xunity.log`.
  - La scelta resta nel profilo del gioco (`"live": "service"`): anche "Riapplica" rimette il traduttore di PatchSmith. Scrivendo a mano `"live": "google"` le frasi nuove le chiede XUnity da solo a Google.
  - Se il plugin non si può preparare (per esempio manca il compilatore C# di Windows), le frasi nuove le chiede XUnity da solo a Google, ma **mai mentre Google è in pausa**: allora restano in originale e la finestra finale lo dice.
- **Memoria condivisa:** la memoria del gioco, le traduzioni automatiche e la memoria comune le scrivono sia l'interfaccia sia il traduttore avviato dal gioco. Chi salva prende un lucchetto (`<file>.lock`), rilegge il file e aggiunge solo le sue modifiche: nessuno cancella le frasi dell'altro. Se il `.lock` per un attimo non si apre (un altro programma lo sta togliendo), si riprova invece di salvare senza lucchetto. Valgono le stesse regole per i testi con problemi (`problemi.json`) e per la provenienza della memoria comune. Un file rovinato a mano viene conservato in `<file>.illeggibile` prima di essere riscritto; i file `.tmp` lasciati da un salvataggio interrotto li toglie il traduttore all'avvio.
- **Traduzioni salvate:** `BepInEx/Translation/<lingua>/Text/_AutoGeneratedTranslations.txt` contiene righe `originale=traduzione`. "Correggi traduzioni" mostra queste righe insieme alla memoria del gioco e, salvando, sostituisce quelle corrette (a gioco chiuso); "Apri il file (avanzato)" apre questo file.
- **Lettere che i font non hanno:** PatchSmith accende in `BepInEx.cfg` la scrittura dei messaggi di Unity nel registro di BepInEx (`[Logging.Disk] WriteUnityLog = true`; nei giochi già installati lo fa il controllo dopo aver giocato). Lì TextMeshPro scrive ogni lettera che i font del gioco non hanno: dopo la partita PatchSmith la annota nel profilo e la sostituisce nelle righe della cache scritte da lui, e da allora la evitano anche la pre-traduzione e il traduttore delle frasi nuove (in memoria resta la traduzione vera).
- **Controllo dopo aver giocato:** lo fa PatchSmith da solo all'apertura (vedi "Dopo aver giocato"). `python -I smith.py status <gioco>` legge `BepInEx/LogOutput.log` e dice se BepInEx e XUnity sono partiti (solo con "Chainloader startup complete": "Chainloader started" c'è anche quando il gioco si chiude subito dopo) e quante frasi sono state tradotte.
- **Versione:** il manifesto `patchsmith-unity.json` ricorda con quale versione di PatchSmith è stata scritta la configurazione (`patchsmith_version`, anche nei manifesti di Unreal e XNA e nelle informazioni dei file tradotti); il controllo dopo aver giocato aggiorna i giochi installati con una versione precedente. Le opzioni di prova di XUnity (`EnableTextPathLogging`, `EnableTranslationHelper`, `CopyToClipboard`) restano sempre spente.
- **Ripristino:**
  - toglie solo i file messi da PatchSmith e quelli creati da BepInEx/XUnity;
  - eventuali mod aggiunte da te restano al loro posto;
  - prima di togliere tutto salva le traduzioni nella memoria (`memory/<gioco>.unity/`), che tornano con "Analizza e traduci".
- **Sicurezza:**
  - non installa se il gioco è aperto, se c'è già un altro loader di mod o se c'è un anti-cheat;
  - tutto ciò che aggiunge è elencato, con le impronte, in `patchsmith-unity.json`;
  - se l'installazione si interrompe, viene annullata.

## Giochi Unreal Engine 4

Per i giochi Unreal (UE4, archivi `.pak` classici) PatchSmith **non modifica i file del gioco** e non scarica nulla.

- **Testi:** legge in sola lettura i `.pak` del gioco e trova i file di localizzazione `.locres` della lingua originale. La lingua viene dal file `.locmeta` (anche se sta in un altro pak), per esempio `DDS/Content/Localization/Game/en/Game.locres`. Valgono anche i pak piccoli "montati" in una cartella più profonda (in Remnant `pakchunk1` parte da `Remnant/Content/`): il pak di patch usa sempre i percorsi completi.
- **Traduzione:** traduce le frasi con il traduttore scelto, proteggendo:
  - segnaposto come `{count}`;
  - tag di stile come `<Bold>…</>`;
  - separatori come `;-;`.
- **Font:** le lettere che le traduzioni ufficiali del gioco in lingue latine usano, il font le sa disegnare; se ne usano di accentate, il font ha tutte le lettere accentate comuni (anche `ì` e `ò`). Un carattere che nessuna lingua del gioco usa diventa il più simile (`—` → `-`); se non si può (per esempio `★`) la frase resta in originale, va nell'elenco "Non scritte nel gioco" e alla prossima traduzione PatchSmith la rifà senza. Senza traduzioni ufficiali con lettere accentate non c'è nessun limite. Su Dungeon Defenders: Awakened (francese, tedesco, spagnolo, portoghese) non cambia nulla.
- **Pak di patch:** scrive un pak nuovo, `Content/Paks/zz_Patchsmith_<lingua>_999_P.pak`, con i `.locres` tradotti **allo stesso percorso** degli originali. Il suffisso `_P` e il numero alto fanno sì che Unreal lo carichi con priorità più alta di tutti gli altri, comprese le patch ufficiali. Il gioco mostra quindi la traduzione al posto dell'inglese.
  - **Nel gioco deve restare selezionata la lingua originale**, di solito English.
- **Verifiche:**
  - il `.locres` tradotto viene riletto e deve avere chiavi e intestazione identiche all'originale;
  - il pak scritto viene riletto e deve contenere esattamente i file previsti.
  - Il pak viene scritto prima con un nome temporaneo e messo al suo posto solo alla fine, quindi un'interruzione si annulla sempre con "Ripristina originali".
- **Ripristino:** toglie solo il pak aggiunto, se la sua impronta corrisponde a quella elencata in `patchsmith-unreal.json`. I pak originali non sono mai stati toccati.
- **Gioco senza `.locres`:** i `.locres` si cercano prima di creare il profilo. Se non ce ne sono, PatchSmith non lascia un profilo vuoto e spiega che i testi del gioco sono dentro gli altri dati, che oggi non sa tradurre; se i `.locres` ci sono ma fuori dagli archivi, o se alcuni archivi non si leggono, dice invece che sono in file che non sa ancora tradurre. Un pak che non si riesce a leggere (formato più nuovo, cifrato o rovinato) si salta, con una riga nel registro, e si cerca negli altri.
- **Aggiornamenti:** `status` segnala se il gioco è stato aggiornato. In quel caso premi "Analizza e traduci": vengono tradotte solo le frasi nuove.
- **Sicurezza:** non installa se il gioco è aperto, se c'è un anti-cheat o se i pak sono firmati (`.sig`), perché un pak aggiunto verrebbe rifiutato.
- **Limiti:**
  - pak versione 3–9 (Unreal 4.x) non cifrati, con file non compressi o zlib;
  - Unreal 5 / IoStore (`.utoc`/`.ucas`) e pak cifrati non sono ancora gestiti;
  - le scritte disegnate nelle texture e i testi scritti direttamente negli asset (non localizzati dagli sviluppatori) restano in originale.

## Giochi XNA / FNA / MonoGame (.NET)

Molti giochi .NET tengono i testi in **risorse .NET**. La versione originale sta dentro l'eseguibile (per esempio `RogueCastle.Resources.LocStrings.resources` in `RogueLegacy.exe`). Accanto c'è un "assembly satellite" per ogni lingua: `de/RogueLegacy.resources.dll`, `fr/...` e così via. Quando il gioco chiede una lingua, .NET cerca prima il suo satellite.

PatchSmith **non modifica i file del gioco** e non scarica nulla:
1. **Lettura:** legge in sola lettura le risorse dall'eseguibile. Il lettore .NET e il lettore dei font XNB sono scritti in Python.
2. **Traduzione:** traduce i testi proteggendo segnaposto e codici come `{0}`, `[x]`, `[Input:12]`, `[Button:LeftStick]` e `\n`.
3. **Font:** in XNA un carattere che manca nel font **fa andare in crash il gioco**. PatchSmith legge i font del gioco (`Content/**/*.xnb`) e usa solo i caratteri che tutti i font di testo sanno disegnare.
   - Se mancano le lettere accentate, l'accento diventa un apostrofo: `è` diventa `e'`, `perché` diventa `perche'`.
   - Virgolette e puntini "tipografici" diventano quelli semplici.
   - Le frasi impossibili da adattare restano in originale.
4. **Satelliti:** crea **nuovi** satelliti, per esempio `en/RogueLegacy.resources.dll` e `it/RogueLegacy.resources.dll`. Li compila con il compilatore C# che c'è in ogni Windows, quello di .NET Framework, e li verifica rileggendoli.
   - Il satellite della lingua originale (`en`) serve ai giochi che scelgono la lingua dal menu.
   - Quello della lingua di arrivo (`it`) serve ai giochi che seguono la lingua di Windows.
5. **Ripristino:** toglie solo i satelliti **creati da PatchSmith**, che riconosce da un marcatore interno. Funziona anche se il manifesto `patchsmith-xna.json` si è perso, e i file del gioco non vengono mai toccati.
   - Se il gioco ha già la lingua di arrivo (una traduzione ufficiale), PatchSmith non traduce e dice di sceglierla nel menu del gioco.
   - Se un font `.xnb` non si riesce a leggere (per esempio è compresso), per sicurezza usa solo caratteri ASCII.
6. **Aggiornamenti:** `status` segnala se il gioco è stato aggiornato.

**Nel gioco deve essere selezionata la lingua originale**, di solito English.

**Limiti:**
- serve un gioco con risorse .NET localizzate (con almeno un satellite). Senza, PatchSmith cerca i file di testo del gioco (JSON, tabelle CSV) e propone la traduzione sperimentale: anche lì le traduzioni si adattano ai font XNB del gioco, perché un carattere mancante lo farebbe chiudere;
- i testi scritti direttamente nel codice e i testi in formati di dati propri del gioco restano in originale;
- gli assembly firmati con nome sicuro non sono ancora gestiti.

## Giochi GameMaker

Molti giochi GameMaker leggono i testi da **file di localizzazione** accanto all'eseguibile, uno per lingua. Per esempio, in Dokimon ci sono `Localization/english_ui.txt` e `Localization/french/ui.txt`.

PatchSmith:
1. **Trova i file** della lingua originale. La lingua la riconosce dal percorso (`english_`, `french/`, `en/`…). I file delle altre lingue non vengono toccati.
2. **Legge il formato "a righe con id"** e riconosce da solo i due tipi di file:
   - **a righe:** `0 Fight`, `5  do next?`. Lo spazio dopo il numero fa parte del testo: serve per attaccarlo a un nome;
   - **a blocchi:** una riga con il solo numero, poi una riga per riquadro di dialogo, chiusa da una riga con `.`. Una voce può avere più sezioni, per esempio prima e dopo una lotta.

   I commenti `//`, i numeri, le righe vuote, i `.` e gli a capo (CRLF o LF) restano **identici byte per byte**. Le righe anomale dei file originali non si toccano.
3. **Traduzione:** traduce proteggendo codici come `/name/` (il nome del giocatore), `/n/` (a capo) e `/region/`. I dialoghi su più righe mantengono lo stesso numero di riquadri.
4. **Font:** in GameMaker un carattere che il font non ha **compare come un quadratino** (▯). PatchSmith legge i font dentro `data.win`, in sola lettura, e adatta i caratteri. Per l'italiano: se il font ha l'accento acuto ma non il grave, `ì` diventa `í`, `ò` diventa `ó` e `ù` diventa `ú` (così, può, più → cosí, puó, piú); altrimenti diventano apostrofi. Non contano i font usati solo per altre lingue, cioè quelli con soprattutto un'altra scrittura (un font coreano, o uno giapponese con qualche lettera come `ç ê ñ`: più di 10 volte i caratteri dell'altra scrittura rispetto alle lettere accentate), né i font di ripiego delle librerie (`scribble_fallback_font`, solo ASCII), né quelli che non hanno nessuna delle lettere accentate delle traduzioni ufficiali del gioco (per esempio il font della chat). Un font con qualche lettera accentata invece conta: può essere proprio quello dei testi inglesi (in Another Door un font ha solo le `Á ç é ô` che usa il file inglese), quindi lì `è` diventa `e'`. Se un font non si riesce a leggere, PatchSmith usa solo caratteri ASCII.
   - **Controlli sulle singole traduzioni**, secondo come questi giochi leggono i file:
     - una `/` in più verrebbe presa per l'inizio di un codice e cancellerebbe il resto della riga, quindi diventa `-` (Attiva/disattiva → Attiva-disattiva);
     - i codici devono essere gli stessi dell'originale, e un codice che finisce la riga viene chiuso con uno spazio;
     - una riga di dialogo non può cominciare con un numero diverso dall'originale, perché il gioco la scambierebbe per un'altra voce;
     - una riga non può diventare vuota né trasformarsi nel `.` di chiusura.

     Una traduzione che non supera i controlli resta in originale, senza bloccare le altre.
5. **Scrittura sicura:** come per i giochi web, l'originale va accanto come `*.patchsmith-originale` con le impronte. Dopo la scrittura il file viene riletto e la struttura deve essere identica. "Ripristina originali" rimette tutto byte per byte.

**Nel gioco resta selezionata la lingua originale**, di solito English, tranne quando la traduzione diventa una lingua nuova (vedi sotto). Se il gioco ha già la lingua di arrivo, PatchSmith non traduce e dice di sceglierla nel menu.

Se modifichi a mano un file già tradotto, PatchSmith se ne accorge: non lo scambia per un aggiornamento del gioco, quindi l'originale inglese non si perde. Per correggere una traduzione usa "Correggi traduzioni".

**File di localizzazione JSON.** Alcuni giochi, per esempio Forager, usano un dizionario per lingua (`local/english.json`, `local/french.json`…). Altri hanno il codice della lingua attaccato al nome (Another Door: `localization/stringsEN.json`, `stringsIT.json`, `stringsJP.json`, `stringsBR.json`), i testi divisi in sezioni (`"menu": {"_play": "Play"}`), commenti `//` e la virgola dopo l'ultima voce: PatchSmith li riconosce tutti, e riscrive il file cambiando solo le frasi tradotte (commenti e impaginazione restano identici byte per byte). PatchSmith:
- **Traduce solo il file della lingua originale.** Gli altri sono le traduzioni ufficiali del gioco e restano intatti. Un file di lingua che non si riesce a leggere viene scritto nel registro.
- **Ogni valore è un testo dell'interfaccia,** anche una parola sola ("wood"). Restano fuori solo gli identificativi rimasti al posto di un testo mancante (`Structure.NuclearFurnace`).
- **Riconosce le convenzioni del gioco** confrontando le traduzioni ufficiali, e le rispetta:
  - **a capo scritti con un segno** (in Forager `*`: `iron*ore`): si traduce la frase intera, poi gli a capo vengono rimessi in punti bilanciati tra le parole, nello stesso numero o meno (`minerale di*ferro`);
  - **paragrafi** (`**`) e **simbolo a inizio paragrafo** (in Forager la `U `): restano al loro posto;
  - **frasi spezzate da un paragrafo** (`U items discarded from your**inventory are instantly**sold instead`): un pezzo senza simbolo, che comincia in minuscolo dopo un pezzo senza punteggiatura finale, continua la frase (se il pezzo prima è una voce d'elenco o ha almeno tre parole: un titolo come `Controls**move with WASD` resta a sé). Si traduce la frase intera e poi la si rimette sulle stesse righe. In Forager sono 42 casi, e le traduzioni ufficiali li riuniscono quasi tutti allo stesso modo;
  - **segnaposto** come `{game mode}`: protetti.
- **Font:** per decidere quali caratteri si possono usare, guarda i font che le traduzioni ufficiali usano davvero. Quelli solo ASCII, come la chat o i numeri, non contano.
- **Lingua nuova invece di sostituire l'originale.** Se il gioco carica da solo tutti i file della cartella delle lingue (PatchSmith cerca la maschera, es. `local/*.json`, tra le stringhe del gioco o nell'eseguibile), la traduzione diventa un file in più, `local/italiano.json`, e l'inglese resta intatto. Serve davvero: in Forager, con la lingua English, i pulsanti del menu principale sono immagini con la scritta inglese già disegnata, e il testo tradotto si vede solo con un'altra lingua.
  - Se il gioco ha già salvato le sue opzioni (Forager: `%LOCALAPPDATA%\Forager\config.txt`), PatchSmith imposta lì la lingua nuova, così il gioco parte già tradotto. Lo fa solo a gioco chiuso, solo se la lingua compare in un unico punto, con una copia di sicurezza in `save-backups/` e toccando solo quel valore. Altrimenti nel gioco si sceglie "italiano" in Opzioni > Lingua.
  - Anche "Riapplica" imposta la lingua aggiunta. Se nel frattempo nel gioco hai scelto un'altra lingua, PatchSmith la lascia.
  - "Ripristina originali" rimette nelle opzioni la lingua di prima, poi toglie la lingua aggiunta. Lo fa solo se è stato PatchSmith a impostarla per quella cartella: le opzioni stanno in `%LOCALAPPDATA%` con il nome del gioco, quindi una copia del gioco le condivide con quello vero.
  - Un file con lo stesso nome che non è di PatchSmith, per esempio una traduzione amatoriale o un `italiano.json` ufficiale arrivato con un aggiornamento, non viene mai sovrascritto né cancellato.
  - Una lingua aggiunta corretta a mano, prima di essere riscritta o tolta, viene messa da parte come `<file>.patchsmith-modificato`, che il gioco non carica. Per correggere una traduzione conviene comunque usare "Correggi traduzioni".
- **Giochi scritti in minuscolo, con le maiuscole come icone.** Forager disegna il testo con un **font a immagini** (una lettera per fotogramma di uno sprite). Le minuscole sembrano maiuscole a blocchi, e le poche maiuscole del font sono icone: `H` il cuore, `EP` la "XP" gialla, `U` il punto elenco, `C` la moneta. PatchSmith:
  - trova la mappa dei caratteri del font a immagini (nel gioco o nell'eseguibile, quando è compilato YYC);
  - riconosce lo stile dai testi: quasi nessuna parola con maiuscole e minuscole insieme;
  - protegge le icone dell'originale (`EP`, `H`) come i segnaposto;
  - scrive la traduzione tutta in minuscolo, tranne le icone e i segnaposto (prima `mostra*FPS:` si vedeva "MOSTRA / P" con la P gialla);
  - adatta i caratteri che il font non ha: `ì` diventa `í`, `ò` diventa `ó`, `"` diventa `'`, `;` diventa `,`.

  I testi con le maiuscole normali, per esempio i messaggi di sistema, restano come li scrive il traduttore e usano i caratteri dei font normali del gioco: il gioco li disegna con quelli, non con il font a immagini.
- **Spazio delle righe.** Per ogni testo PatchSmith guarda la riga più lunga e il numero di righe dell'originale e delle traduzioni ufficiali con lo stesso font. Se una riga tradotta esce da quello spazio, gli a capo si ridistribuiscono tra le parole, aggiungendo righe solo fin dove le versioni del gioco ne hanno (`vsync:` diventa `sincronizzazione*verticale:`, come il francese `synchro*verticale :`).
  - Un'etichetta corta che non ci sta resta com'è se almeno due traduzioni ufficiali la lasciano uguale all'originale (`vsync:`, `extras`).
  - Le etichette di una riga più lunghe di tutte le versioni del gioco, con il traduttore locale, si chiedono più corte una volta, con quel numero di caratteri (vedi "Lettere che i font non hanno ed etichette troppo lunghe").
  - I testi che restano troppo lunghi vengono elencati nel registro (con il testo inglese), da accorciare con "Correggi traduzioni": sono nell'elenco "Troppo lunghi", e mentre scrivi la finestra dice quanto spazio c'è.

**Tabelle CSV con una colonna per lingua.** Alcuni giochi tengono tutti i testi in una tabella (Megaloot: `translations.csv`, con `id,english,french,german,...,koreana,schinese,tchinese`). PatchSmith:
- riconosce le colonne dal nome della lingua o dal codice (`english`, `en`, `English (US)`, `koreana`, `schinese`, `textEN`…) e vuole una colonna che non è una lingua, la chiave (per esempio `id`): senza, il gioco potrebbe cercare i testi proprio con il loro valore inglese, e la tabella non si tocca;
- traduce la colonna della lingua originale al suo posto: nel gioco resta selezionata quella lingua. Una colonna italiana già piena è la traduzione ufficiale (una colonna vuota no: è una lingua prevista ma non tradotta);
- riscrive solo le celle tradotte: separatore (`,` `;` o tab), virgolette, BOM e a capo restano come li ha scritti il gioco, e il file si rilegge per controllo. Molti giochi leggono la tabella dividendo le righe sulle virgole (in Megaloot nessun testo inglese ha una virgola, al suo posto c'è `;`): se la tabella non usa le virgolette alla maniera dei CSV, nelle traduzioni `,` diventa `;` e `"` diventa `'`, come fanno i testi del gioco. Una traduzione con un a capo in più resta in originale;
- si salta un `.csv` con accanto il suo `.csv.import` (sorgente di Godot: il gioco usa il file compilato).

**Senza file di lingua riconosciuti** PatchSmith propone la traduzione sperimentale dei file di testo che trova (vedi "Come funziona"): il profilo ricorda che il gioco è GameMaker, quindi anche lì le traduzioni si adattano ai font del gioco e le etichette di una parola dei file `{chiave: testo}` si traducono.

**Limiti:**
- l'italiano è più lungo dell'inglese: alcuni testi possono uscire un po' dal loro spazio (il registro li elenca);
- i testi scritti nel codice del gioco (dentro `data.win`, o nell'eseguibile per i giochi compilati YYC) restano in originale;
- altri formati di localizzazione (INI, file JSON in UTF-16) non sono ancora gestiti.

## Lingua ufficiale: se l'italiano c'è già

Prima di tradurre, PatchSmith controlla se il gioco ha **già la traduzione ufficiale** nella lingua di arrivo. Se c'è, non fa una traduzione automatica, che sarebbe peggiore: usa quella ufficiale.

**Come lo capisce:**
- **Steam:** per un gioco installato da Steam legge, offline, la cache di Steam (`appcache/appinfo.vdf`) con le lingue ufficiali. Riconosce l'installazione anche attraverso junction e collegamenti.
- **Cartella che non è un'installazione Steam:** il gioco si può riconoscere confrontando gli eseguibili della cartella con i percorsi di avvio dei giochi nella cache di Steam. Il riconoscimento è prudente:
  - non contano gli installatori (vcredist, DirectX, .NET...) né i nomi generici (`start_protected_game.exe`, `game.exe`, `launcher.exe`...);
  - non conta un eseguibile avviato da più giochi;
  - a pari merito non vince nessuno.

  Anche così vale solo se i file del gioco non dicono nulla **e** il nome della cartella somiglia a quello del gioco; altrimenti è solo una nota, che non blocca la traduzione.
- **File del gioco**, motore per motore:
  - Unreal: `.locres` per lingua nei pak, compresi quelli di UE 4.26+/UE5 se non sono cifrati; contano solo i testi del gioco, non quelli del motore o dei plugin;
  - Unity: lingue attive di I2 Localization e Unity Localization;
  - XNA/.NET: satelliti di lingua;
  - GameMaker: file per lingua (anche JSON con commenti e il codice attaccato al nome, come `stringsIT.json`) e colonne piene delle tabelle CSV.

Le varianti contano: per il cinese la scrittura (zh-TW e zh-CN sono lingue diverse e non si scambiano mai); per le altre lingue si preferisce il codice esatto, poi la lingua senza regione.

**Cosa fa:**
- **Unreal:** imposta la lingua in `GameUserSettings.ini` dell'utente, sezione `[Internationalization]`. Cambia solo le chiavi `Language`/`Locale`/`Culture` già presenti, altrimenti aggiunge `Culture`. Usa il nome esatto della cartella della lingua, per esempio `it-IT`. Cerca il file in `%LOCALAPPDATA%\<Progetto>`, nella cartella del gioco e in `Documenti\My Games\<Progetto>`. Lo fa solo a gioco chiuso, e non scrive se il gioco ha una sua impostazione della lingua: in quel caso dà le istruzioni. Ogni riga mantiene il suo "a capo", anche se nel file sono misti.
- **Unity con I2 Localization:** imposta la preferenza "I2 Language", nel registro di Windows, al nome esatto della lingua (per esempio "Italiano"). Tocca solo quel valore, a gioco chiuso, e solo se il gioco è già stato avviato una volta. Se il gioco ha modificato I2 per salvare la lingua a modo suo (per esempio `LastSelectedLanguage`), non tocca nulla e dà le istruzioni. Anche quando la imposta, è un tentativo: se il gioco parte in un'altra lingua, va scelta nel suo menu.
- **Steam:** dice dove sceglierla. Il menu "Proprietà > Generali > Lingua" compare solo se la lingua è selezionabile da Steam; altrimenti va scelta nelle opzioni del gioco. PatchSmith non modifica i file di Steam.
- **Negli altri casi:** spiega esattamente come attivarla.

Ogni impostazione cambiata viene registrata in `languages/`. "Ripristina originali" la rimette com'era, riga per riga, ma solo dove nel frattempo non l'hanno cambiata il gioco o l'utente. Lo fa per ultima: se non ci riesce (gioco aperto, file di sola lettura), il resto del ripristino va avanti e il registro resta per riprovare.

Se non trova le impostazioni del gioco, la prima volta chiede di avviarlo una volta. Le volte successive dice di scegliere la lingua nel menu.

Se è ancora installata una traduzione automatica di PatchSmith, il messaggio lo dice.

Per tradurre comunque: `--translate-anyway` da riga di comando, oppure "Sì" nella domanda dell'interfaccia. In questo caso il controllo della lingua ufficiale non viene fatto, e una lingua impostata prima da PatchSmith torna com'era, così la traduzione installata si vede. `smith auto` esce con 0 anche quando ha gestito la lingua ufficiale.

PatchSmith **non modifica** componenti non ufficiali, come i file che sostituiscono Steam nelle copie non originali.

## Configuratore: impostazioni del gioco cambiate da fuori

Alcuni giochi hanno l'italiano dentro di sé, ma non hanno un'impostazione per sceglierlo. Il configuratore cambia quello che serve **nei file del gioco stesso**, sempre in modo reversibile:
- **backup verificato** accanto a ogni file toccato (`.patchsmith-originale` + `.patchsmith-info`, come per le traduzioni): "Ripristina originali" rimette tutto, anche senza registro;
- **registro** in `configurations/`: cosa è stato cambiato, quando, e i consensi dati;
- **tutto o niente**: se un file non si può scrivere, quelli già scritti tornano com'erano;
- **a gioco chiuso**, con controllo dello spazio libero e dei file di sola lettura;
- se un aggiornamento del gioco sostituisce un file, il nuovo file diventa l'originale (il vecchio backup resta conservato) e la modifica si riapplica.

**Modifiche agli eseguibili.** Si fanno solo con il tuo consenso: PatchSmith lo chiede la prima volta per ogni gioco, poi lo ricorda (per esempio per riapplicare dopo un aggiornamento). Dalla riga di comando: `smith auto <cartella> --modify-executables`. La firma digitale degli eseguibili modificati non è più valida: se Smart App Control di Windows è attivo, PatchSmith non li modifica, perché Windows li bloccherebbe. Qualche antivirus potrebbe segnalarli.

Le conoscenze sui singoli giochi stanno nelle **schede** (`patchsmith/games.py`). Ogni scheda trova le modifiche per **schema di byte** verificato, mai per indirizzo fisso: una versione diversa del gioco viene rifiutata senza toccare nulla.

### Scheda: KINGDOM HEARTS HD 1.5+2.5 ReMIX
- **Come sceglie la lingua:** ognuno dei 6 programmi (KH1, Re:CoM, KH2, BBS, menu di avvio, Theater) chiede la lingua alla piattaforma (Steam/Epic) e la converte in un codice (inglese 1, tedesco 3, francese 4, spagnolo 5, italiano 6). Per qualsiasi altra risposta usa un valore predefinito: l'inglese. Non ha altre impostazioni della lingua. I testi di tutte queste lingue sono già installati.
- **Cosa fa PatchSmith:** cambia il valore predefinito, 1 byte in ciascuno dei 6 eseguibili. Così la risposta "inglese" porta all'italiano, mentre francese, tedesco e spagnolo scelti dalla piattaforma restano rispettati. Il gioco funziona come con l'italiano scelto da Steam: testi, menu, font e immagini con le scritte italiani; le voci restano quelle originali.
- **Cosa non tocca:** il programma di avvio con la protezione (`ReMIX.exe`) e i componenti di Steam.
- Prima della prima modifica copia i salvataggi in `save-backups/` (solo letti).
- Se hai il gioco installato da Steam con l'italiano selezionabile, PatchSmith ti indica il menu di Steam invece di modificare gli eseguibili.

In arrivo con lo stesso impianto: risoluzione, modalità finestra/schermo intero e limite FPS (per Kingdom Hearts stanno in `config1525.dat`).

## Cosa funziona oggi

| Motore | Stato |
|---|---|
| Electron / NW.js / HTML5 | **Supportato**: dati JSON + menu (iniettore). Provato su Sudoku Mansion |
| RPG Maker MV/MZ | Sperimentale, con richiesta di conferma (`www/data/*.json` + menu su canvas). I suoi eventi mescolano dialoghi e comandi dei plugin, spesso parole giapponesi usate come identificatori: senza un adapter dedicato tradurli può rompere il gioco |
| Unity (Mono, 32/64 bit) | **Supportato**: installa e configura in automatico BepInEx + XUnity.AutoTranslator, che traducono i testi dentro il gioco mentre giochi. Provato su Rubinite (Unity 6) |
| Giochi con i file cifrati (es. Electron con GemShell) | **Tradotti mentre giochi**, se il gioco ha `resources/app/preload.js` in chiaro che gira nella sua pagina (`contextIsolation:false`, `sandbox:false`, come con GemShell). Il file cifrato non si apre e non si tocca. In fondo a `preload.js` (con backup) PatchSmith aggiunge uno script che traduce i testi della pagina: quelli già tradotti da `patchsmith-dizionario.json`, quelli nuovi chiesti al traduttore di PatchSmith, che parte e si chiude con il gioco. I numeri si tolgono e si rimettono ("{{A}} minutes"); le lettere accentate che i font non hanno diventano e', a'. Le frasi nuove di una schermata si chiedono insieme e il traduttore locale le fa in parallelo (prova: 40 frasi in circa 4 secondi). Una frase in attesa resta nascosta al massimo un secondo, poi si vede l'originale finché non arriva la traduzione. Senza quel `preload.js` il gioco non è traducibile, e PatchSmith lo spiega senza toccare nulla. Provato su Now That's a Big Dragon (prova dei testi a schermo). Le cartelle degli emulatori di Steam (`steam_settings`…) non si scansionano mai |
| Unity con Unity Localization (anche IL2CPP) | **Supportato** per i giochi IL2CPP: traduce le tabelle dei testi inglesi dentro i loro bundle, senza mod, anche quando il catalogo controlla l'impronta dei bundle. Anche con il catalogo binario (`catalog.bin`). Nei giochi Mono si aggiunge a XUnity, per i dialoghi animati. Provato su SWORN; Big Dig con `catalog.bin` |
| Unity IL2CPP senza Unity Localization | Rilevato. In arrivo (serve BepInEx 6) |
| Unreal Engine 4 (pak non cifrati) | **Supportato**: traduce i `.locres` e li aggiunge come pak di patch, senza toccare i pak originali. Provato su Dungeon Defenders: Awakened |
| Unreal Engine 5 / IoStore, pak cifrati | Rilevati. Non ancora supportati |
| XNA / FNA / MonoGame con risorse .NET localizzate | **Supportato**: aggiunge satelliti tradotti, con i caratteri adattati ai font del gioco. Provato su Rogue Legacy |
| XNA / FNA / MonoGame senza risorse localizzate | Rilevati. File di testo del gioco (JSON, CSV): sperimentale, con i caratteri adattati ai font del gioco. Testi nel codice: in arrivo |
| GameMaker con file di localizzazione esterni | **Supportato**: traduce i file della lingua originale con backup, adattando i caratteri ai font del gioco: file a righe (Dokimon), dizionari JSON per lingua (Forager; anche con sezioni, commenti e il codice attaccato al nome, come Another Door) e tabelle CSV con una colonna per lingua (come Megaloot) |
| GameMaker con testi solo nel codice (data.win) | Rilevato. In arrivo |
| Godot 4 | **Supportato** se il gioco usa le tabelle delle traduzioni di Godot (`.translation`, importate da CSV o PO), anche con il pacchetto dei dati incorporato nell'eseguibile. PatchSmith legge la tabella inglese dal pacchetto senza toccarlo: testi compressi (smaz), chiavi come impronte. Poi traduce e mette accanto all'eseguibile `override.cfg` (il modo previsto da Godot per cambiare le impostazioni del progetto) con un piccolo script, che all'avvio sostituisce la tabella inglese con il ponte verso l'italiano e aggiunge la lingua italiana. Codici di colore BBCode (`[color=...]`) protetti. Pacchetto cifrato: non si apre. Godot 3: in arrivo. Provato su You Know The Drill (Godot 4.4) |
| Ren'Py, KiriKiri, Wolf | Rilevati. Adapter in arrivo |

**Limiti:**
- La traduzione automatica non ha contesto: la qualità è da traduzione automatica.

## Traduttori

| Traduttore | Cosa serve | Quanto | Note |
|---|---|---|---|
| **Traduttore locale** (consigliato) | Un'installazione una tantum: Ollama (1,5 GB) e un modello TranslateGemma di Google (4B: 3,3 GB; 12B: 8,1 GB). Meglio con una scheda video NVIDIA | **Nessun limite** | Traduce sul tuo PC: niente account, niente quote, niente blocchi; dopo l'installazione funziona anche senza internet |
| **DeepL** | Una tua chiave gratuita: account "DeepL API Free" su deepl.com/pro-api (chiede una carta per la verifica, non addebita nulla) | 500.000 caratteri al mese (Forager: circa 42.000) | API ufficiale, qualità migliore per l'italiano. Errori chiari: chiave sbagliata, caratteri del mese finiti. Il pulsante **Verifica** accanto alla chiave la prova subito e dice quanti caratteri hai usato questo mese |
| **MyMemory** | Niente (nessun account); con la tua email si arriva a 50.000 caratteri al giorno. L'email non si conferma: serve solo ad alzare il limite. **Verifica** controlla che sia scritta bene e che MyMemory la accetti | 5.000 caratteri al giorno senza email, 50.000 con | API pubblica ufficiale. Una richiesta per riga: è più lento |
| **Google gratuito** | Niente | Non dichiarato | **Non ufficiale**: Google non lo prevede per i programmi (il suo `robots.txt` lo esclude) e da settembre 2026 rifiuta spesso queste richieste fin dalla prima. Nel menu si chiama "Google gratuito (non ufficiale)"; quando lo scegli una finestra spiega i blocchi (una volta per sessione) |

**Traduttore locale.**
- **Cosa installa:** Ollama 0.40.4 (release ufficiale da GitHub, licenza MIT, impronta SHA-256 controllata) e il modello scelto, dal registro ufficiale di Ollama. I modelli Gemma hanno la loro licenza (Gemma Terms of Use), che accetti scaricandoli. Tutto va in `components/ollama`, accanto a PatchSmith: niente installazioni in Windows. PatchSmith ricorda dove si trova (`%LOCALAPPDATA%\PatchSmith\local-translator.json`): un'altra copia di PatchSmith sullo stesso PC, o la versione successiva estratta in un'altra cartella, usa lo stesso invece di riscaricare i gigabyte (prima viene un'installazione completa della copia stessa). Il server parte solo durante la traduzione, risponde solo a questo PC (127.0.0.1, porta propria) e si chiude alla fine, anche se PatchSmith si chiude male. Non usa e non tocca un Ollama già installato.
- **I due modelli**, provati su 100 testi di Forager con una RTX 3060 Ti 8 GB:

  | Modello | Tempo per testo | Forager intero (circa 1.700 testi) | Qualità |
  |---|---|---|---|
  | **Qualità (12B)**, predefinito se scaricato | circa 2,5 s (il modello sta per il 62% nella scheda video, il resto in memoria) | circa 1 ora | Sbaglia meno nomi di oggetti e meno grammatica |
  | **Veloce (4B)** | circa 0,5 s (tutto nella scheda video) | circa 15 minuti | Buona sulle frasi, più errori sui nomi brevi |

  Prima di cominciare PatchSmith mostra il tempo stimato, e dopo le prime righe lo ricalcola sul tuo PC. Se il modello è lento (per esempio senza scheda video NVIDIA), lo dice e consiglia il modello Veloce. Prima di installare, l'interfaccia propone il modello adatto alla memoria della scheda video (12B da 8 GB in su, altrimenti 4B); dopo l'installazione una frase di prova dice se il modello sta nella scheda video e quanto è veloce.
- **Contesto:** il modello traduce una riga per volta, con un contesto fisso ("testo di un videogioco, dai del tu al giocatore"). Nei file di lingua con traduzioni ufficiali (es. Forager) riceve anche:
  - la chiave del testo, senza il numero finale (`configWeather`, `itemName`), come indizio di dove compare;
  - fino a 3 traduzioni ufficiali del gioco, solo per capire il senso. Per l'italiano si usano francese, tedesco e spagnolo, ma non spagnolo e portoghese insieme, perché il modello li copiava. Se le righe non corrispondono, il modello non le riceve.
  - Se la risposta è identica a una traduzione ufficiale, si riprova senza riferimenti, e la si tiene solo se torna uguale: "corona" va bene, "saque" no. Si scarta anche una risposta con lettere che l'italiano non usa, come "crânio".

  Nelle prove su 48 testi difficili di Forager le traduzioni buone sono passate dal 43% al 64% con il 4B e dal 73% all'80% con il 12B ("weather" diventa "meteo", "fossil" "fossile", "exit" "esci").
- **Controlli su ogni traduzione:** PatchSmith toglie quello che il modello aggiunge di suo:
  - virgolette e note tra parentesi;
  - più traduzioni alternative per un nome ("edificio; costruzione");
  - il punto finale o i puntini dove l'originale non li ha.

  Se l'originale comincia in minuscolo, anche la traduzione resta in minuscolo. Scarta invece le risposte con testo di contorno ("Translation: …"), i rifiuti, l'alfabeto sbagliato, i numeri cambiati, le ripetizioni, gli a capo in più (o in meno, in un testo di più righe), le lunghezze anomale e i segnaposto persi.
  - Una risposta scartata si riprova con parametri diversi. Se aveva cambiato un numero, si riprova con l'istruzione di tenerli com'erano ("200% more" tradotto "il doppio" diventa "il 200% in più").
  - I numeri piccoli scritti in lettere sono accettati solo con parole non ambigue: "1 or more" tradotto "uno o più" va bene, ma "un" e "sei" no.
  - Codici rovinati dal modello (`[[4]Spine`, `[0]`, `[[2]〕`) vengono rimessi a posto. Gli a capo che il modello aggiunge a un testo di una riga diventano spazi. Se un codice manca, si riprova con l'istruzione di copiarli tutti; c'è un terzo tentativo per i testi scartati due volte.
  - Un valore con il segno all'inizio (`-1 Dash Charge`, `+25% damage`) è protetto come un codice.
  - Se è sbagliato solo un segnaposto, si traducono a parte i pezzi tra i codici.
  - Se non va ancora, PatchSmith riprova subito in un altro modo (vedi "Testi scartati e traduzioni da rifare").
- **Più righe insieme:** con il modello Veloce la scheda video traduce 4 righe alla volta, circa 1,3 volte più veloce (misurato su una RTX 3060 Ti: 0,10 s a riga contro 0,14). Il modello Qualità, che non sta tutto nella scheda video, va una riga alla volta.
- **Durante la traduzione non avviare il gioco:** la scheda video è occupata. **Ferma** si interrompe tra una riga e l'altra, e le traduzioni fatte restano salvate.

**Testi scartati e traduzioni da rifare** (vale con ogni traduttore; i semi solo con quello locale). Prima un testo scartato dai controlli si riprovava con gli stessi parametri, e la risposta era la stessa: "rilancia" non serviva. Ora:
- **Subito, nello stesso avvio**, i testi scartati si riprovano in un altro modo: ogni frase e ogni pezzo tra i codici da solo, con semi nuovi (ricavati dal testo e dal numero del tentativo, e temperature un po' più alte: a temperatura 0 il seme non cambia nulla). Il modello resta quello che hai scelto: PatchSmith non carica da solo il Qualità (12B) al posto del Veloce. I testi che restano si ritraducono, se vuoi, con il 12B da "Correggi traduzioni".
- Quelli che restano in originale vanno in `memory/<gioco>.it.problemi.json`, con il motivo (per esempio "scartata dai controlli (numeri diversi)") e i tentativi fatti. Alla prossima "Analizza e traduci" si riprovano **con semi tutti nuovi**. La finestra finale dice cosa ha già provato PatchSmith e li elenca con "Mostra i rimasti".
- **Traduzioni che non si possono scrivere nel gioco** (caratteri che i font del gioco non hanno, forma diversa dall'originale, righe in più): vanno in `problemi.json` con il motivo. Se le aveva scritte PatchSmith, con "Analizza e traduci" le rifà **subito** con l'istruzione giusta (per esempio "non usare: →"); se le trova "Riapplica", che non traduce, le rifà alla traduzione successiva. La traduzione di prima resta finché non ne arriva una migliore. Se le avevi corrette tu, restano in memoria e la finestra finale ti dice di sistemarle con "Correggi traduzioni".
- **Traduzioni salvate ricontrollate:** a ogni traduzione le voci scritte in automatico (anche da versioni precedenti o da Google) si ricontrollano con i controlli di adesso: lettere o parole di un'altra lingua (senza contare tag, codici e titoli tra virgolette), numeri, righe, segnaposto, un nome del gioco tradotto, una traduzione ufficiale di un'altra lingua copiata. Quelle che non passano si rifanno, partendo da semi nuovi (con gli stessi del primo giro la risposta sarebbe la stessa); se rifarle non riesce, o la nuova traduzione non va ancora, resta annotata in `problemi.json` come sospetta, anche se si può scrivere nel gioco, con i tentativi fatti. Le tue correzioni non si toccano mai. Dopo 6 tentativi una traduzione si lascia com'è. Una traduzione ufficiale copiata conta solo per le frasi: una parola uguale in due lingue ("Oro" in spagnolo e in italiano) è normale.
- **Imprevisti:** un errore inatteso del traduttore durante i nuovi giri non ferma il lavoro: le traduzioni fatte si scrivono nel gioco e i testi rimasti vanno in `problemi.json`.

**Memoria comune di tutti i giochi** (`memory/comune.en-it.json`, vale con ogni traduttore). Oltre alla memoria di ogni gioco, PatchSmith tiene i **testi brevi** (fino a 4 parole: "options", "back", "press any key") già tradotti per gli altri giochi, e non li ritraduce.
- Le frasi lunghe non ci vanno: tra giochi diversi quasi mai si ripetono uguali, e il loro senso dipende dal gioco.
- Ci entrano solo i testi **tradotti in quella traduzione** che passano i controlli di adesso (non le voci vecchie della memoria del gioco) e le correzioni a mano. Accanto, in `comune.en-it.origine.json`, c'è da dove viene ogni voce: traduttore, modello, versione di PatchSmith e gioco, oppure "corretta a mano".
- Le voci senza provenienza (messe dalle versioni precedenti) si usano solo se passano i controlli di adesso; altrimenti il testo si traduce di nuovo e la voce viene sostituita. Una voce che traduce un nome del gioco (per esempio il suo titolo) non vale per quel gioco. I controlli guardano la forma, non il senso: "Obsidian" tradotto "Obsidiano" passa, e va corretto a mano.
- Se correggi un testo breve con "Correggi traduzioni", la correzione passa subito nella memoria comune e la usano i giochi tradotti dopo (anche un testo rimasto in originale che hai tradotto tu; una voce già corretta a mano in un altro gioco resta quella). Una correzione uguale all'originale (per esempio un nome lasciato in inglese) vale solo per quel gioco.
- Non si usano dizionari di singole parole: una frase tradotta parola per parola viene male ("light" può essere "luce" o "leggero").

**Come PatchSmith evita di farsi bloccare dai servizi online.**
- **Limitatore prudente**, condiviso tra interfaccia e riga di comando:
  - Google: al massimo una richiesta ogni 4–6 secondi, 8 al minuto, 60 all'ora;
  - prima di cominciare mostra quante richieste servono e quanto tempo ci vorrà.
- **Al primo rifiuto si ferma subito**, senza insistere, e si ricorda fino a quando non riprovare:
  - rifiuto alla prima richiesta (l'accesso non è accettato, non è un limite superato): 1 giorno, poi 3, poi 7, poi il servizio resta disattivato finché non lo riattivi tu;
  - rifiuto dopo richieste andate a buon fine (limite di ritmo): 1 ora, poi 6, poi 24.

  Insistere durante un blocco può allungarlo. PatchSmith non cerca altre strade per aggirare un blocco di Google: per continuare subito si sceglie un altro traduttore, meglio quello locale (i messaggi di blocco lo consigliano, e prima di "Analizza e traduci" un traduttore in pausa propone "Usa il traduttore locale").
- Le traduzioni già fatte restano sempre salvate: si riparte da dove ci si era fermati.

## Roadmap

- **Fase 1:**
  - Unity IL2CPP (BepInEx 6 + XUnity IL2CPP);
  - Ren'Py (`tl/` + unrpyc);
  - RPG Maker (rvpacker-txt-rs);
  - giochi web dentro `app.asar`.
- **Fase 2:** Godot 3 e i testi di Godot fuori dalle tabelle delle traduzioni; i testi nel codice di GameMaker (`data.win`).
- **Fase 3:** Unreal 5 / IoStore e motori delle visual novel giapponesi.

## Test

**Tutte le prove insieme:** `python -I test/run_all.py`. Ogni prova riceve una cartella vuota sua, con dentro i suoi profili, la sua memoria e le sue impostazioni (una cartella `PATCHSMITH_DATA` temporanea solo per lei). Anche `%LOCALAPPDATA%`, `%APPDATA%`, `%TEMP%` e la cartella dell'utente sono cartelle di prova sue. Così le opzioni, i salvataggi e i crash dei giochi veri non si toccano, e una prova non vede quello che ha lasciato un'altra.
- L'uscita di ogni prova va in `logs/<prova>.txt`, nella cartella di lavoro. Una prova che dura più di 15 minuti si ferma, insieme ai programmi che ha avviato.
- Alla fine controlla che i dati veri di PatchSmith (profili, memoria, impostazioni…) non siano cambiati.
- La cartella di lavoro si cancella se tutto va bene; `--keep` la conserva, `--work <cartella>` la sceglie (ad esempio su un altro disco).
- Con dei nomi esegue solo quelle prove: `python -I test/run_all.py test_unity test_xna`.
- `test_gui` si fa su un gioco web finto creato dalla prova (`test/fake_web_game.py`): per qualche secondo si apre la finestra di PatchSmith. Parte con delle impostazioni già salvate, che alla fine devono essere ancora com'erano.
- `test_safety` vuole la **copia** di un gioco: `--safety <copia> <file.json relativo>`. Senza la copia viene saltata, e lo dice.

Le singole prove:
- `python -I test/test_regressions.py <cartella-vuota>`: i problemi già risolti sui giochi veri, perché non tornino. Copre:
  - Unity 6: BepInEx parte al primo MonoBehaviour, Unity 2022 non si tocca, la configurazione esistente si conserva;
  - interfacce IMGUI (anche in un modulo del gioco o in `Assembly-UnityScript`, non nelle librerie di terzi), le DLL del gioco e chiavi scritte nella configurazione di XUnity;
  - catalogo di Unity Localization che controlla l'impronta (CRC) dei bundle, anche con il testo a una posizione dispari;
  - numeri con la virgola ('2.5') nel controllo del traduttore locale;
  - nomi dei tasti nei giochi web (`<kbd>`, `class="key"`), con node.js anche sull'iniettore vero;
  - le opzioni di prova di XUnity sempre spente nella configurazione;
  - i registri veri, ridotti e anonimi, in `test/data/signals/` (vedi il loro `README.md`), anche gli eventi di crash di Windows e il diario delle frasi.
- `python -I test/test_checks.py <cartella-vuota>`: il controllo dopo aver giocato, con giochi finti e i registri veri ridotti di `test/data/signals/`. `%TEMP%`, la cartella dell'utente e `%LOCALAPPDATA%` sono cartelle di prova. Copre:
  - Unity: crash all'avvio della copia giusta (quello dell'altra copia, nelle stesse cartelle, non conta) corretto con il punto d'ingresso di BepInEx; `LogOutput.log` senza "startup complete"; Google fermo nel `Player-prev.log`; comando del traduttore con un Python che non c'è; configurazione cambiata; diario con frasi provvisorie (righe della cache sostituite, la riga corretta a mano resta) e frasi perse rimesse in memoria; file temporanei; tutto in sola lettura senza scrivere nulla, con il gioco aperto senza scrivere nulla, poi corretto; un secondo controllo che non fa nulla; un crash anche dopo la correzione che propone "Ripristina originali" senza farlo;
  - un gioco installato con una versione vecchia (come Rubinite): Google in pausa detto con la data, traduttore di PatchSmith rimesso, versione aggiornata, "Installa il traduttore locale" se non c'è un traduttore;
  - file tolti dall'antivirus rimessi una volta, e da fare se tornano a mancare;
  - gioco web: file rimessi originali dal gioco (Riapplica da sola), aggiornati ("Analizza e traduci"), crash nel registro di Windows ("Ripristina originali" da confermare; eventi 1000 e 1026 veri letti anche nella codifica di Windows);
  - GameMaker (lingua aggiunta non scelta o tolta), Unreal (lingua e pak non caricato), XNA (satellite tolto, gioco aggiornato);
  - `smith.py check --dry-run` (codice 2, nulla scritto), la finestra dell'esito, il rapporto e il file `<gioco>.controllo.json` che non è un profilo e non viene cancellato;
  - i casi trovati nella verifica: registri oltre 2 MB (una partita lunga non è un crash all'avvio, gli errori in fondo al `Player.log` contano), installazione interrotta (solo "Ripristina originali"), profilo senza cartella, registro di Unreal con la cartella del progetto, crash di Windows di prima dell'ultimo controllo, PatchSmith aggiornato, traduttore dal vivo partito senza traduttori, riga di comando con un profilo rovinato (gli altri giochi si controllano, codice 1);
  - il controllo all'apertura che non ritraduce le frasi del diario (restano da fare con "Ritraduci", il diario non va avanti, le frasi perse si salvano lo stesso) e poi le ritraduce con il pulsante; un gioco Unity 6 con `LogOutput.log` incompleto e nessun crash (solo da sapere), con un crash nel registro di Windows ("non accetta mod" da confermare) e "anche dopo la correzione" solo se la correzione c'è stata.
  - le correzioni di "Correggi traduzioni" salvate con il gioco Unity aperto scritte dal controllo (a gioco chiuso, una volta; una riga cambiata a mano resta), un'altra versione con la configurazione da rimettere detta in un solo messaggio, il rapporto con le correzioni in attesa e le lettere dei font; "Ritraduci" con il traduttore locale non installato (le frasi perse si salvano, il pulsante è "Installa il traduttore locale").
- `python -I test/test_preliminary.py <cartella-vuota>`: i controlli prima di partire e le scelte predefinite sicure, con processi aperti, spazio libero, Steam e scheda video simulati. Copre:
  - gioco aperto (anche per Riapplica e Ripristina), lingue sconosciute, uguali o fuori elenco, cartella in cui non si può scrivere (anche dentro Programmi; il file di prova non resta), poco spazio, Google in pausa (Installa o Usa il traduttore locale), traduttore locale non installato (e continuare senza: ci si ferma prima di toccare il gioco);
  - lo stesso gioco in un'altra cartella: identità da Unity, Steam, eseguibili e nome (senza i programmi che tanti giochi hanno uguali, come `createdump.exe` o DOSBox); una copia delle traduzioni che non riesce non ferma "Analizza e traduci"; una copia che riusa la memoria dell'originale (anche una correzione a mano), un gioco spostato che porta con sé il suo profilo, nessuna proposta per un'altra lingua o una memoria vuota;
  - traduttore predefinito locale anche se non è installato; memoria video da `nvidia-smi`, registro di Windows e WMI (al limite di 4 GB non si sa; `nvidia-smi` e PowerShell mai dalla cartella corrente) e modello consigliato; `smith.py local` con il modello consigliato e `--install` con la frase di prova;
  - la frase di prova dopo l'installazione (scheda video, solo processore, frase rimasta in originale, server che non parte) e i messaggi di blocco che consigliano il traduttore locale.
- `python -I test/test_legacy.py <cartella-vuota>`: i file scritti dalle versioni precedenti, con chiavi e codici italiani (`patchsmith/legacy.py`). Profili, impostazioni, stato dei controlli, righe del diario, registri, informazioni dei backup e manifesti di prima letti con i nomi inglesi, le mappe di file e testi lasciate come sono, i file nuovi invariati; le letture di PatchSmith con i file vecchi (profilo trovato dalla sua cartella, informazioni del backup, stato e comando del plugin di BepInEx); `migrate_formats` riscrive profili, registri e impostazioni tenendo ogni originale come `.italian-keys`, mai le memorie delle traduzioni.
- `python -I test/test_user_data.py <cartella-vuota>`: la cartella dei dati (`PATCHSMITH_DATA`). Controlla i percorsi con e senza la variabile, `smith auto`/`status`/`restore` su un gioco finto, il comando del traduttore dal vivo per Unity, il salvataggio delle impostazioni e il passaggio dai nomi italiani delle cartelle a quelli inglesi (spostamento senza perdere nulla, componenti uniti, profilo chiesto con il percorso di prima).
- `python -I test/test_run_all.py <cartella-vuota>`: il programma che esegue tutte le prove. Controlla che ogni prova abbia dati e cartelle sue, che una prova bloccata si fermi insieme ai programmi che ha avviato, che il controllo dei dati veri si accorga di ogni cambiamento e che l'elenco delle prove senza argomenti sia giusto.
- `python -I test/web/make_page.py`: genera `test/web/index-inline.html`, la pagina che collauda l'iniettore nel browser (testi dinamici, attributi, `<option>`, pulsanti anche di una parola come `PLAY` e `OK`, canvas, entità HTML).
- `python -I test/test_formats.py <cartella-vuota>`: i formati dei file di lingua dei giochi nuovi e i limiti dei loro motori, con giochi finti costruiti sul modello di quelli veri (nessun testo dei giochi veri). Copre:
  - JSON con commenti `//` e `/* */`, virgole in fondo e sezioni (stile Another Door): letto, riscritto identico byte per byte tranne le frasi tradotte (anche BOM, CRLF, numeri come `1.50`, escape `\u`), un valore non stringa cambiato che non si scrive, i file rovinati che restano errori;
  - GameMaker con `localization/stringsEN.json` e le traduzioni ufficiali: codice attaccato al nome (`stringsEN`, `stringsBR`, `stringsSC`, `stringsTC`), famiglia `strings{L}.json`, etichette di una parola e tag `[yellow1]…[/c]` tradotti, font giapponese e `scribble_fallback_font` che non tolgono `è` e `ò`, solo il file inglese cambiato, ripristino identico; con `stringsIT.json` la lingua ufficiale;
  - tabelle CSV: stile Megaloot (divisa sulle virgole, `koreana`, `schinese`, `tchinese`, colonna italiana vuota, `,` che diventa `;`) e una tabella CSV vera (BOM, `;`, CRLF, virgolette, `""` e a capo nelle celle), riscritte cambiando solo le celle tradotte (anche una tabella divisa a mano con una cella come `"Hero" rises`, e con righe vuote tra i gruppi di testi); tabelle senza chiave, con una lingua sola o non UTF-8 rifiutate; un gioco GameMaker con `translations.csv` tradotto e ripristinato (una traduzione con un a capo in più resta in originale, scritta nel registro); la colonna italiana piena come lingua ufficiale; la tabella in un gioco web e il sorgente di Godot saltato (anche nel registro);
  - GameMaker senza file di lingua riconosciuti: traduzione sperimentale con il motore nel profilo (anche nei profili vecchi), parole sole tradotte e caratteri adattati al font (`è` → `e'`); lo stesso per XNA, con i font simulati; non diventano file di lingua un elenco di oggetti (come `PluginInfo.json` dei suoni), un salvataggio e una famiglia di file per lingua con un elenco di oggetti (gli identificativi non si traducono);
  - font di GameMaker per un'altra scrittura (giapponese con qualche accento), di ripiego e senza nessuna lettera delle traduzioni ufficiali esclusi; un font con il cirillico e poche lettere accentate (come quello di Another Door) e un font latino senza accenti che contano ancora;
  - pagina `test/web/index-inline.html` con i pulsanti PLAY e OK, e le stringhe del JavaScript che si vedono (`textContent`, `fillText`, `label:`, `add.text`) separate da identificativi, classi, font e confronti;
  - Unreal senza `.locres` (nessun profilo, il motivo), con i `.locres` fuori dagli archivi (motivo diverso), con un pak illeggibile (saltato, profilo creato) e con un pak montato in una cartella più profonda (tradotto in un pak di patch con i percorsi completi);
  - i file saltati nel registro: `.json` non validi o troppo grandi, file con pochi testi, file web troppo grandi.
- `python -I test/test_font_length.py <cartella-vuota>`: lettere che i font non hanno ed etichette troppo lunghe, per tutti i motori, con giochi, font e registri finti (`test/fake_unity.py`, `test/data/signals/`; nessun gioco vero, nessuna rete, traduttori finti in memoria). Copre:
  - font di Unity: la tabella cmap di un `.ttf`, i font TextMeshPro nei bundle (con l'albero dei campi) e in `resources.assets` (senza, riconosciuti dalla forma), un font dinamico con il suo `.ttf`, nessun limite quando non si sa, il risultato tenuto in `font.json` e riletto dopo un aggiornamento del gioco;
  - Unity Localization: `è` → `e'` solo nella tabella del gioco (in memoria resta `è`, la tabella francese non cambia), un carattere che non si può adattare (`→`) in "Non scritte nel gioco", le lettere annotate dal controllo valide anche se un font le ha, nessun cambiamento con un font che ha tutte le lettere, un errore imprevisto nella lettura dei font che non ferma la scrittura;
  - XUnity: `WriteUnityLog` acceso all'installazione e rimesso dal controllo; i caratteri mancanti letti da righe vere (anche `\U0001F41A` e il formato vecchio); la copia del gioco riconosciuta anche nei giochi IL2CPP; il controllo che in sola lettura non scrive nulla, poi adatta le righe della cache scritte da PatchSmith (non quella cambiata a mano) e annota le lettere nel profilo; dopo, cache e traduttore delle frasi nuove le evitano; Unity Localization con il `Player.log`: lettera annotata e tabella riscritta, quelle dei soli testi del gioco ignorate; "Riapplica" che scrive adattata una correzione fatta in "Correggi traduzioni" (e la conta come scritta); un errore imprevisto nel controllo delle lettere che non nasconde gli altri problemi; un `BepInEx.cfg` con byte non UTF-8 riscritto uguale;
  - Unreal: con il francese il Latino-1 completo (anche `ì` e `ò`), il giapponese che non conta, un carattere che nessuna lingua usa (`★`) che lascia l'originale; senza indizi nessun limite;
  - etichette: lo spazio (con e senza traduzioni ufficiali), le troppo lunghe chieste più corte con il numero di caratteri (anche nel prompt del traduttore locale), la più corta valida tenuta, quella ancora lunga in `problemi.json` con il testo originale e nell'elenco "Troppo lunghi", una volta sola, le correzioni a mano lasciate, i traduttori online che solo annotano (e il traduttore locale che dopo le chiede più corte, una volta), "Ritraduci" con lo spazio, un errore imprevisto che non ferma la traduzione, l'esito.
- `python -I test/test_gui.py <copia-di-un-gioco>`: collaudo automatico dell'interfaccia grafica, con il traduttore di test e il ripristino finale. Controlla anche che durante un lavoro non ne parta un secondo e che i comandi del traduttore siano disattivati. Controlla poi:
  - il titolo della finestra di fine operazione per ogni esito;
  - il registro salvato in `logs/`, anche con un errore imprevisto iniettato (finestra "Errore imprevisto", traceback nel file) e con un errore in un comando della finestra (registro `interfaccia` anche dopo l'installazione del traduttore locale, finestra senza "Riprova");
  - che un profilo rovinato non lasci i pulsanti disattivati e che "Usa il traduttore locale" dopo un errore ripeta "Analizza e traduci" con le stesse scelte;
  - il controllo del gioco scelto (finestra di fine), il controllo all'apertura (nessuna finestra, i pulsanti in fondo solo con qualcosa da fare, registro `controllo`; se hai già avviato altro parte appena finisce), "Dettagli" con "Ripristina originali" e "Crea rapporto" per la cartella di quel gioco, l'avviso di PatchSmith aggiornato con la finestra aperta (una volta sola);
  - "Ripristina originali" e "Crea rapporto" (lo zip va in `reports-gui-test`, accanto alla copia, mai sul Desktop vero; con quella cartella bloccata va in `logs/`);
  - il primo avvio senza traduttore salvato ("Traduttore locale" da installare con "Installa…" e il modello per la scheda video simulata), i controlli prima di partire (gioco aperto e "Riprova", cartella protetta, lingua, Google in pausa, traduttore locale da installare: l'operazione non parte), una copia del gioco in `copy-gui-test` che riusa le traduzioni e il pulsante "Ritraduci N frasi" dopo il controllo all'apertura; l'installazione chiesta prima di partire (con la frase di prova riuscita l'operazione riparte, altrimenti una finestra e niente operazione) e il controllo all'apertura che aspetta la chiusura di una finestra aperta.

  - "Correggi traduzioni…": una riga corretta nella finestra e salvata (memoria valida con il valore nuovo, "Riapplica" da sola che cambia il file del gioco) e il pulsante "Correggi traduzioni" nella finestra di fine con testi rimasti.

  Senza `PATCHSMITH_DATA` profilo e impostazioni vanno in `data-gui-test`, accanto alla copia. Le impostazioni che c'erano già in quella cartella tornano com'erano alla fine.
- `python -I test/test_package.py <cartella-vuota>`: il pacchetto da pubblicare (circa 40 secondi): programma, `PatchSmith.exe` e Python portatile con Tkinter, senza dati di chi lo usa né componenti scaricati; lo zip con la sola cartella `PatchSmith`; `smith.py` con il Python portatile; `PatchSmith.exe` che sceglie il Python portatile anche con un Python installato; un pacchetto con dei dati dentro che non si cancella.
- `python -I test/test_paths.py <cartella-vuota>`: i percorsi completi di chi usa solo l'interfaccia, dall'inizio alla fine, con giochi finti e traduttori finti (nessuna rete, nessun Ollama, nessun gioco vero); si apre per qualche secondo la finestra di PatchSmith, che a metà si chiude e si riapre. Copre:
  - un gioco web nuovo: "Analizza e traduci", il controllo dopo aver giocato, "Correggi traduzioni" con la correzione scritta nel gioco, "Ripristina originali" (file identici) e "Riapplica", "Crea rapporto";
  - il gioco aperto prima di partire ("Riprova") e durante il lavoro (GameMaker: traduzioni pronte ma non scritte), Google in pausa ("Continua comunque", poi "Usa il traduttore locale"), Google che blocca a metà ("Traduzione interrotta"), il traduttore locale non installato ("Continua comunque" senza toccare il gioco, poi "Installa ora" e la traduzione che riparte da sola), testi rimasti in originale ("Correggi traduzioni" sull'elenco giusto);
  - un gioco GameMaker con una traduzione che i font non sanno disegnare (la finestra di fine e "Correggi traduzioni" portano a "Non scritte nel gioco");
  - un gioco Unity Mono: il traduttore delle frasi nuove di PatchSmith e il registro di Unity acceso; una correzione salvata con il gioco aperto; alla riapertura di PatchSmith il controllo con i registri veri ridotti (Google fermo, lettere che il font non ha) che corregge da solo, scrive la correzione rimasta in attesa e propone "Ritraduci"; "Dettagli", "Ripristina originali", "Riapplica" e il rapporto;
  - ogni finestra mostrata dice il passo dopo in italiano, senza nomi tecnici.
- `python -I test/test_corrections.py <cartella-vuota>`: "Correggi traduzioni" con traduttori finti (nessuna rete, nessun Ollama) e giochi finti; si apre per qualche secondo la finestra di PatchSmith. Copre:
  - l'elenco e i suoi filtri (rimasti, troppo lunghi con lo spazio misurato nel gioco o senza, non scritte nel gioco, numeri e segnaposto, un'altra lingua, sospetti come "Esportare" al posto di "Esporta", corrette a mano), la ricerca e i controlli mentre scrivi;
  - la finestra: titolo, conteggi, riga modificata, Esc, Ctrl+Invio e gli altri tasti, traduzione vuota o con un segnaposto sbagliato, chiusura con correzioni non salvate, un elenco di 750 righe a pagine;
  - "Salva e applica al gioco" su un gioco web: memoria valida, traduzioni automatiche di prima conservate, memoria comune come correzione a mano, testo tolto dai rimasti, "Riapplica" da sola che cambia il file del gioco;
  - GameMaker: "Troppo lunghi" con lo spazio delle versioni del gioco e una correzione con un carattere che i font non hanno ("Non scritte nel gioco" e il pulsante nella finestra di fine);
  - Unity: righe della cache di XUnity nell'elenco (rimaste in originale, provvisorie del diario), sostituite salvando mentre tutte le altre restano identiche, correzione rimandata con il gioco aperto, riga cambiata a mano dopo la correzione che resta, ritraduzione di una riga della cache;
  - "Ritraduci selezionati" (domande nuove anche per le righe corrette a mano, e di nuovo nuove premendolo ancora; scelta del modello, traduttore locale mancante), "Ritraduci tutto con Qualità (12B)" e "Ricomincia da zero" (solo per le prove: i pulsanti non ci sono più), una memoria di una versione vecchia con poche voci annotate (solo le correzioni vere sono "Corrette a mano"), "Apri il file (avanzato)" con il Blocco note, un file delle traduzioni rovinato, il pulsante "Correggi traduzioni" nelle finestre di fine.
- `python -I test/test_outcomes.py <cartella-vuota>`: l'esito di ogni operazione e le sue finestre, con giochi finti (web, GameMaker, Unity Mono, IL2CPP e Unity Localization) e il traduttore di test. Copre:
  - tradotto, in parte (con i testi rimasti da mostrare), riapplicato, ripristinato, già originale, niente da riapplicare;
  - gioco aperto (nessun file toccato), Google in pausa scritto in uno stato dei traduttori di prova (nessuna richiesta), lingua ufficiale;
  - Unity IL2CPP senza tabelle, Unity Localization con l'impronta (CRC) controllata (senza "serve BepInEx 6"), anti-cheat;
  - gli errori trasformati in esiti (anche quelli imprevisti) e una finestra con un titolo diverso per ogni esito;
  - i pulsanti del traduttore locale ("Usa" se è installato, "Installa" se no) e le finestre delle operazioni che non toccano il gioco (rapporto, installazione: niente "Riprova");
  - "Crea rapporto": i file attesi ci sono, chiave DeepL ed email no (neanche dentro un registro o in un indirizzo web);
  - chi traduce le frasi nuove mentre giochi (Google, traduttore di PatchSmith, nessuno, con la spiegazione e "Installa il traduttore locale") e un file rovinato in `profiles/` (saltato);
  - nessun testo che arriva all'interfaccia rimanda a `smith.py`; la riga di comando scrive il comando per rimediare sotto l'errore e in `smith services`.
  - le traduzioni che il gioco non saprebbe mostrare, scartate quando si scrivono: la finestra di fine e "Correggi traduzioni" portano all'elenco "Non scritte nel gioco".
- `python -I test/test_xunity_service.py <cartella-vuota> [<cartella _Data di un gioco Unity Mono>]`: traduttore delle frasi nuove per XUnity con traduttori di prova (nessuna rete, nessuna scheda video). Copre:
  - risposte, segnaposto `{{A}}` intatti, memoria, diario delle frasi, porta già in uso dallo stesso gioco, chiusura insieme al processo del gioco (503 mentre si chiude; porta liberata subito anche con una frase in traduzione, che finisce e si salva), `.tmp` vecchi tolti all'avvio;
  - traduttori in fila (uno che non risponde o in pausa si salta; 500 solo se nessuno risponde) e quali userebbe il servizio;
  - frasi scartate dai controlli: mai 500, risposta di ripiego (anche a pezzi tra i `{{A}}`) o l'originale, e cosa ne dice il diario; la lingua della risposta (frasi italiane con "se", "tu", "su", "no" non scambiate per spagnolo; una proposta rimasta in inglese sì);
  - Google con i limiti prudenti pieni (frase rimandata, nessuna richiesta) e in pausa; DeepL che chiede di aspettare due minuti (un solo nuovo tentativo dopo pochi secondi);
  - memoria salvata insieme da due programmi e da 4 processi (nessuna voce persa, voci tolte che non tornano), memoria comune, file rovinato, lucchetto (anche un `.lock` che per un attimo non si apre);
  - configurazione `CustomTranslate` (senza anti-spam, 2500 caratteri), Google, Endpoint vuoto con Google in pausa.
  - Con la cartella `_Data` anche la compilazione del plugin, il comando e la rimozione.
- `python -I test/test_unity_loc.py <cartella-vuota> [<bundle con le tabelle dei testi>]`: Unity Localization, con bundle e giochi IL2CPP finti (`test/fake_unity.py`, nessun file di giochi veri). Copre:
  - bundle senza compressione, LZ4 e LZMA, anche letti a pezzi (anche un blocco senza compressione grande quanto il bundle, e un LZMA più corto del previsto); bundle troncati; le tabelle degli asset non si traducono;
  - la lingua dal nome, dal contenuto (`english`, `localization_en`, `chinese(simplified)`) o dal nome della lingua; l'italiano trovato così come lingua ufficiale;
  - il catalogo che controlla l'impronta: solo quella del bundle tradotto va a 0 (anche con due bundle), backup del catalogo, Riapplica, una nuova "Analizza e traduci" (il catalogo resta nel profilo anche se nel gioco ha già il CRC 0), il catalogo e il bundle rimessi dal gioco visti dal controllo dopo aver giocato e ritradotti, un aggiornamento che cambia solo il catalogo (il controllo toglie di nuovo il CRC da solo), Ripristina identico; il catalogo senza impronte (come SWORN) non si tocca;
  - nessun file modificato con `catalog.bin`, con l'impronta che non si trova, con l'anti-cheat, senza la lingua di partenza (il motivo dice le lingue del gioco; scelta una di queste, si traduce) o con l'italiano già nel gioco;
  - con un bundle vero anche la riscrittura identica, i testi cambiati e riletti, il flusso con il traduttore di prova su una copia e il ripristino identico.
- `python -I test/test_unity.py <cartella-vuota>`: adapter Unity con un gioco e pacchetti finti, senza download (installazione, configurazione, mod dell'utente, antivirus, interruzioni, manifesto alterato, ripristino). Poi chi traduce le frasi nuove con "Analizza e traduci" e "Riapplica" (con un plugin finto): il traduttore di PatchSmith anche scegliendo Google e anche dopo "Ripristina originali", il traduttore scelto salvato nel profilo, nessun traduttore disponibile, compilatore C# mancante con Google libero o in pausa. La pre-traduzione dei giochi Unity di oggi: un modulo del gioco con le sue stringhe (non le librerie di terzi), `data.unity3d` compresso con un testo a cavallo di due blocchi (non le immagini), un bundle LZMA, la tabella dei testi inglese (non le altre lingue), un file grande letto a pezzi, un bundle rovinato, il limite di tempo dei bundle. Infine i nomi del gioco nel profilo (da `app.info`; un nome tolto a mano non torna) e la cache di XUnity: righe sbagliate scritte da PatchSmith o dal traduttore dal vivo rifatte e sostituite, righe cambiate a mano e nomi lasciati come sono.
- `python -I test/test_unreal.py <cartella-vuota>`: adapter Unreal con un gioco finto (pak v9 e `.locres` costruiti dal test). Copre lettura, traduzione, cambio lingua, pak altrui, manifesto alterato, installazione interrotta, pak firmati, aggiornamento del gioco e ripristino.
- `python -I test/test_xna.py <cartella-vuota>`: adapter XNA/.NET con un gioco finto, creato dal test con il compilatore C# di Windows, e un font XNB finto. Controlla le traduzioni con il vero ResourceManager di .NET e copre i font, gli accenti, il ripristino, i satelliti altrui, il manifesto alterato, l'installazione interrotta e l'aggiornamento del gioco.
- `python -I test/test_placeholders.py`: protezione dei codici di gioco, comune a tutti i motori. Copre `{0}`, `%s`, `[Input:12]`, `\n` scritto come testo, i codici RPG Maker (`\C[3]`, `\nc<Nome>`), i tag di colore e font di GameMaker (`[yellow1]`, `[/c]`, `[/]`) e i percorsi Windows.
- `python -I test/test_limiter.py <cartella-vuota>`: limitatore e traduttori online con risposte simulate (nessuna rete, orologio finto). Copre:
  - finestre e pause;
  - stop al primo 429 / pagina "Sorry" / redirect, pausa ricordata tra un avvio e l'altro, livelli e disattivazione;
  - righe non allineate senza raffiche, errori di rete, Ferma durante una pausa, stima del tempo;
  - DeepL (chiave, lingue, 403/456/429) e MyMemory (email, 500 byte, quota del giorno);
  - credenziali lette dalle impostazioni.
- `python -I test/test_local.py <cartella-vuota>`: traduttore locale senza rete e senza scheda video. Usa un server simulato in memoria e un Ollama simulato (`test/fake_ollama.py`) avviato davvero su 127.0.0.1. Copre:
  - formato del prompt, una richiesta per riga;
  - controlli e secondo tentativo (testo di contorno, alfabeto, rifiuti, numeri, ripetizioni, a capo, non tradotta), risposte troncate;
  - nessun falso scarto: "Ecco…", "Mi dispiace…", cinese e giapponese, "1.000", cifre larghe, virgolette di altro stile;
  - pulizia di quello che il modello aggiunge, e testo rimasto uguale (riprovato una volta, poi tenuto come nome);
  - contesto nel prompt (chiave e traduzioni ufficiali riga per riga), chiave copiata dal modello scartata, correzione sui numeri e numeri in lettere;
  - segnaposto persi e ritraduzione a pezzi, anche con **Ferma** durante il ripiego;
  - una riga troppo lenta resta in originale senza fermare le altre; server caduto;
  - errori comprensibili (modello mancante, memoria);
  - **Ferma** a metà con il salvataggio di quanto fatto, e la scheda video liberata;
  - stima del tempo;
  - avvio e chiusura del server, con un ambiente pulito (le variabili `OLLAMA_*` dell'utente non passano), server che non parte, **Ferma** durante l'avvio, porta occupata da un altro programma;
  - installazione: zip con percorsi pericolosi rifiutato, cartella rovinata rifatta, scaricamento ripreso da dove era;
  - messaggio quando non è installato e modello scelto non scaricato;
  - testi scartati: con un traduttore che sbaglia con i semi 42, 7, 11 il testo si traduce nello stesso avvio (anche con l'Ollama simulato e il suo registro delle richieste); un testo sempre scartato va in `problemi.json`, non in memoria, il secondo avvio usa semi tutti nuovi e il messaggio finale non dice "rilancia"; i testi che il Veloce scarta non fanno caricare da soli il Qualità (12B);
  - nomi del gioco: `app.info` con "Cup Dog Games" (traduzione uguale all'originale, nessuna richiesta, la memoria comune vecchia non vale), informazioni di versione di un eseguibile, nomi di una parola che sono parole comuni;
  - memoria comune: voci vecchie che non passano i controlli saltate e sostituite, solo i testi tradotti adesso (con la provenienza), correzioni a mano riconosciute;
  - traduzioni salvate rifatte (numeri sbagliati, un'altra lingua) senza toccare le correzioni a mano, e lasciate dopo 6 tentativi;
  - traduzioni che il gioco non sa mostrare (un carattere che il font non ha): quella automatica rifatta subito con l'istruzione, quella corretta a mano annotata e detta nell'esito, anche dopo "Riapplica";
  - traduzioni salvate rifatte che non vanno ancora (rifatte uguali, o con la scrittura nel gioco): semi nuovi a ogni avvio, annotate come sospette e lasciate dopo 6 tentativi; un errore imprevisto nei nuovi giri non ferma la scrittura nel gioco; la memoria comune non prende una traduzione nuova in un'altra lingua; "riferimento copiato" solo per le frasi.
- `python -I test/test_gamemaker_json.py <cartella-vuota>`: file di localizzazione JSON (stile Forager) e tasto Ferma. Copre:
  - riconoscimento delle lingue e delle convenzioni (`*` = a capo, simbolo `U`), senza falsi positivi;
  - a capo bilanciati, frasi spezzate da `**` tradotte intere (ma non titoli ed elenchi), righe vuote `* *`, segnaposto `{con spazi}`;
  - font: quelli solo ASCII non contano;
  - si traduce solo il file della lingua originale, con ripristino identico;
  - **Ferma** durante la traduzione: gioco non toccato, si riparte da dove ci si era fermati;
  - attesa del 429 interrotta subito e messaggio comprensibile.
- `python -I test/test_gamemaker.py <cartella-vuota>`: adapter GameMaker con un gioco finto (file a righe e a blocchi, una seconda lingua, un `data.win` minimo con tre font). Copre il formato, la scelta dei file, i font e gli accenti, il flusso completo con il traduttore di test, il ripristino identico e il caso del gioco che ha già l'italiano.
- `python -I test/test_official_language.py <cartella-vuota>`: lingua ufficiale con uno Steam finto (appinfo.vdf costruito dal test), un gioco Unreal finto con l'italiano (impostazione e ripristino di GameUserSettings.ini) e un gioco Unity finto con I2 (preferenza nel registro, in una chiave di prova che viene cancellata).
- `python -I test/test_configurator.py <cartella-vuota> [<cartella di Kingdom Hearts>]`: configuratore e scheda di Kingdom Hearts su un'installazione finta (eseguibili PE costruiti dal test). Copre:
  - riconoscimento ed eseguibili di versioni diverse;
  - consenso;
  - rifiuti: Smart App Control, gioco aperto, sola lettura;
  - guasti a metà, con tutto rimesso com'era;
  - aggiornamento del gioco e ripristino identico.

  Con la cartella del gioco vero ripete la prova su **copie** dei suoi eseguibili: la cartella del gioco non viene toccata.
- `python -I test/test_added_language.py <cartella-vuota>`: lingua aggiunta e giochi in minuscolo (stile Forager), con un gioco finto: `data.win` minimo (nome, stringhe, sprite di un font a immagini, maschera `local/*.json`), anche compilato YYC (maschera e mappa nell'eseguibile). Copre:
  - stile delle maiuscole, icone protette, minuscole, spazio delle righe;
  - `local/italiano.json` creato con l'inglese intatto, nessun file di PatchSmith che il gioco caricherebbe come lingua;
  - rilancio, senza scambiare la lingua aggiunta per una ufficiale;
  - lingua impostata nelle opzioni salvate (ds_map in esadecimale) e rimessa con "Ripristina originali" a file identico; con la lingua in più punti non si tocca nulla;
  - passaggio da una versione precedente, che aveva tradotto english.json al suo posto.
- `python -I test/test_language_regressions.py <cartella-vuota>`: i casi difficili della lingua ufficiale. Copre:
  - riconoscimento Steam prudente: installatori, nomi generici, eseguibili condivisi, pari merito, junction, cache rovinata;
  - pak montati in sottocartelle (v9 e v11);
  - cinese per scrittura;
  - GameUserSettings con "a capo" misti, chiavi doppie, UTF-16 rovinato, sola lettura e gioco aperto;
  - Unity: catalogo binario, I2 personalizzato, errori isolati;
  - "traduci comunque" con GameMaker e Unreal;
  - ripristino dalla riga di comando con la cartella.
- `python -I test/test_cjk.py`: riconoscimento dei testi in giapponese, cinese e coreano. Verifica anche che nulla cambi per l'alfabeto latino.
- `python -I test/test_safety.py <copia-di-un-gioco> <file.json>`: scenari critici, da lanciare dopo `smith auto <copia> --backend fake` (meglio con `run_all.py --safety`, che fa tutto; se il profilo manca, la prova lo crea). Copre la cartella superiore, l'aggiornamento del gioco, il backup alterato e il ripristino completo. Senza `PATCHSMITH_DATA` profilo e memoria vanno in `safety-test-data`, accanto alla copia.

**Questi test vanno usati solo su copie, mai su un gioco vero.**

## Pacchetto da pubblicare

`python -I smith.py package` prepara, accanto alla cartella del programma, `PatchSmith-rilascio/PatchSmith` e lo zip `PatchSmith-<versione>-windows.zip`: chi lo scarica lo estrae e fa doppio clic su `PatchSmith.exe`, anche su un PC senza Python.

- **Cosa c'è dentro:** il programma (`PatchSmith.pyw`, `smith.py`, `patchsmith/`, i sorgenti dei plugin, `data/`, `LEGGIMI.md`, `README.md` in inglese, `docs/`, `LICENSE`), `PatchSmith.exe` e in `runtime/` un **Python portatile**: le sole parti di un Python installato che servono a PatchSmith (Tkinter compresa; senza prove, IDLE, pip), circa 42 MB, 16 MB nello zip con Python 3.15. Il file `._pth` lo isola dalle variabili d'ambiente e dal registro: un altro Python installato sul PC non conta.
- **Cosa non c'è:** i dati di chi usa PatchSmith (profili, memoria, registri, impostazioni…), le prove, i componenti scaricati (BepInEx, XUnity, Ollama: si scaricano quando servono, chiedendo prima).
- `PatchSmith.exe` usa il Python portatile se c'è, se no quello installato. Il traduttore che parte con i giochi (Unity, giochi web dal vivo) usa il Python con cui gira PatchSmith, quindi anche lui quello portatile.
- Prima di creare lo zip PatchSmith prova il Python portatile in un ambiente senza Python (solo le cartelle di Windows nel PATH): deve partire e avere i moduli che servono, Tkinter compresa.
- Nello zip c'è una sola cartella `PatchSmith`: estratta sopra quella di prima la aggiorna e lascia i dati come sono.
- `--python <cartella>` usa un altro Python (consigliato per il rilascio: il più recente da python.org); `--output <cartella>` cambia dove va il pacchetto. Un Python recente senza installarlo: dalla cartella `amd64/` della versione su python.org (es. `https://www.python.org/ftp/python/3.15.1/amd64/`) si scaricano `core.msi`, `exe.msi`, `lib.msi` e `tcltk.msi` e si estraggono con `msiexec /a <file>.msi TARGETDIR=<cartella> /qn` (estrazione: niente in "Programmi" né nel registro). Il pacchetto della 0.1.0 è fatto con Python 3.15.1 (Tcl/Tk 9.0.4); tutte le prove passano sia con il 3.15 sia con il 3.11. Un pacchetto in cui qualcuno ha già lavorato (con dei dati dentro) non si cancella.

## Aggiungere un adapter

Un adapter deve saper fare quattro cose:
1. trovare i testi;
2. estrarli;
3. reinserirli;
4. verificare il risultato.

Guarda `adapter_webjson.py` (dati) e `adapter_webui.py` (menu web), e come li usa `project.py`. Le librerie esterne con licenza GPL vanno usate solo come programmi esterni, per restare in licenza MIT.
