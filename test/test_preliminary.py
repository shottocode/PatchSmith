"""Tests of the checks before starting and of the safe default choices (point 5).

Usage: python -I test/test_preliminary.py <empty_temp_folder>
PatchSmith's data go to <cartella>/dati, the "installed" local translator to <cartella>/ollama (empty: not
installed); Steam, open processes, free space and graphics card are simulated. No real game, no network,
no program started. Covers:
- automatico.controlli_preliminari: game open, From/To languages, folder that cannot be written to (also in the
  Program Files folder), little space, translator paused (local one installed or not), local translator not
  installed, and what is checked for Analyze and translate, Reapply and Restore; the test file does not stay in the
  folder;
- the same game in another folder: identity (Unity, Steam, executables, name), a copy reuses the translation memory
  of the original (also the translations fixed by hand), a moved game takes its game profile with it;
- default translator: always the local one (even if not installed); Google "often blocked";
- graphics card: nvidia-smi, Windows registry, WMI (at the 4 GB limit it cannot be told) and the recommended model;
- test of the local translator after installation (backend.prova_locale): graphics card, processor only, error;
- limiter messages that recommend the local translator.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import types

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import fake_web_game as gioco_web_finto  # noqa: E402
from patchsmith import (auto as automatico, backend, gui, limiter as limitatore, language as lingua, ollama,
                        project as progetto, system as sistema, steam)  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def codici(problemi):
    return [p.codice for p in problemi]


def per_codice(problemi, codice):
    return next((p for p in problemi if p.codice == codice), None)


def gioco_web(cartella, exe=None):
    gioco_web_finto.crea(cartella)
    if exe:
        open(os.path.join(cartella, exe), "wb").write(b"MZ")
    return cartella


def gioco_unity(cartella, nome_exe, azienda, prodotto):
    dati = os.path.join(cartella, nome_exe + "_Data")
    os.makedirs(dati)
    open(os.path.join(cartella, nome_exe + ".exe"), "wb").write(b"MZ")
    open(os.path.join(dati, "globalgamemanagers"), "wb").write(b"\x00" * 16 + b"2022.3.10f1\x00")
    with open(os.path.join(dati, "app.info"), "w", encoding="utf-8") as f:
        f.write("%s\n%s" % (azienda, prodotto))
    return cartella


class ServerFinto:
    """In place of ollama.Server for backend.prova_locale: answers and share of the model on the graphics card."""

    def __init__(self, risposta="Inizia la partita", vram=100, errore=None):
        self.risposta, self.vram, self.errore = risposta, vram, errore
        self.chiuso = False

    def avvia(self):
        if self.errore:
            raise self.errore

    def chiama(self, percorso, dati=None, timeout=180):
        if percorso == "/api/chat":
            return {"message": {"content": self.risposta}, "done_reason": "stop", "eval_count": 5,
                    "eval_duration": 100_000_000, "prompt_eval_count": 20}
        if percorso == "/api/ps":
            return {"models": [{"name": "translategemma:4b-it-q4_K_M", "size": 100, "size_vram": self.vram}]}
        return {}

    def chiudi(self):
        self.chiuso = True


# --- checks before starting ------------------------------------------------------------------------------------

def prova_controlli(base):
    gioco = gioco_web(os.path.join(base, "giochi", "Lantern Keeper"), "Lantern Keeper.exe")
    prima = sorted(os.listdir(gioco))
    problemi = automatico.controlli_preliminari(gioco, "finto", "traduci", lingua.AUTO, "it")
    controlla("gioco pronto: nessun problema, e il file di prova della scrittura non resta (%s)" % codici(problemi),
              problemi == [] and sorted(os.listdir(gioco)) == prima)

    # game open: "Try again", for all the operations; if it cannot be checked, the operation decides
    vero = sistema.gioco_aperto
    sistema.gioco_aperto = lambda _c: True
    try:
        tutte = {op: codici(automatico.controlli_preliminari(gioco, "finto", op, lingua.AUTO, "it"))
                 for op in ("traduci", "riapplica", "ripristina")}
        aperto = automatico.controlli_preliminari(gioco, "finto", "traduci", lingua.AUTO, "it")[0]
    finally:
        sistema.gioco_aperto = vero
    controlla("gioco aperto: primo controllo, con 'Riprova', per tradurre, riapplicare e ripristinare",
              all(c == ["GIOCO_APERTO"] for c in tutte.values()) and aperto.azione == "riprova"
              and "Riprova" in aperto.messaggio)

    def errore_sistema(_c):
        raise sistema.ErroreSistema("prova")
    sistema.gioco_aperto = errore_sistema
    try:
        problemi = automatico.controlli_preliminari(gioco, "finto", "traduci", lingua.AUTO, "it")
    finally:
        sistema.gioco_aperto = vero
    controlla("controllo dei processi non riuscito: nessun avviso qui (lo dice l'operazione stessa)", problemi == [])

    # languages
    casi ={("Inglesse", "it"): None, ("en", "Italianoo"): None, ("xx", "it"): "continua", ("it", "it"): None,
            ("en", "it"): "ok", (lingua.AUTO, "it"): "ok", ("en", "pt-BR"): "continua"}
    sbagliati = []
    for (da, a), atteso in casi.items():
        trovati = [p for p in automatico.controlli_preliminari(gioco, "finto", "traduci", da, a) if p.codice == "LINGUA"]
        ottenuto = "ok" if not trovati else trovati[0].azione
        if ottenuto != atteso:
            sbagliati.append("%s->%s: %s" % (da, a, ottenuto))
    stessa = automatico._problemi_lingue("it", "it")
    controlla("lingue: sconosciute e uguali fermano, un codice fuori elenco si puo' usare comunque%s" % (
        " (sbagliati: %s)" % sbagliati if sbagliati else ""), not sbagliati and "stessa lingua" in stessa[0].messaggio)
    controlla("lingue: si controllano solo per Analizza e traduci",
              "LINGUA" not in codici(automatico.controlli_preliminari(gioco, "finto", "riapplica", "Inglesse", "it")))

    # folder that cannot be written to (Program Files, controlled folder access, antivirus)
    vera = automatico.prova_scrittura
    automatico.prova_scrittura = lambda _c: PermissionError(13, "Accesso negato")
    vecchio_pf = os.environ.get("ProgramFiles")
    try:
        os.environ["ProgramFiles"] = os.path.join(base, "altrove")
        fuori = {op: per_codice(automatico.controlli_preliminari(gioco, "finto", op, lingua.AUTO, "it"),
                                "SOLA_LETTURA") for op in ("traduci", "riapplica", "ripristina")}
        os.environ["ProgramFiles"] = os.path.join(base, "giochi")
        dentro = per_codice(automatico.controlli_preliminari(gioco, "finto", "traduci", lingua.AUTO, "it"),
                            "SOLA_LETTURA")
    finally:
        automatico.prova_scrittura = vera
        if vecchio_pf is None:
            os.environ.pop("ProgramFiles", None)
        else:
            os.environ["ProgramFiles"] = vecchio_pf
    controlla("cartella protetta: si ferma (nessuna azione) per tutte le operazioni, con 'amministratore' e l'accesso "
              "controllato alle cartelle", all(p and p.azione is None for p in fuori.values())
              and "amministratore" in fuori["traduci"].messaggio and "Accesso controllato" in fuori["traduci"].messaggio)
    controlla("cartella protetta dentro Programmi: lo dice e spiega come spostare il gioco",
              dentro and "Programmi" in dentro.messaggio and "Sposta la cartella" in dentro.messaggio)
    un_file = os.path.join(base, "un-file")
    open(un_file, "w").close()
    controlla("prova di scrittura vera: si' in una cartella (niente resta), errore se non e' una cartella",
              vera(gioco) is None and sorted(os.listdir(gioco)) == prima and isinstance(vera(un_file), OSError))

    # free space
    vero_spazio = automatico.shutil.disk_usage
    automatico.shutil.disk_usage = lambda _p: types.SimpleNamespace(total=10 ** 12, used=0, free=50 * 1024 ** 2)
    try:
        spazio = {op: [p for p in automatico.controlli_preliminari(gioco, "finto", op, lingua.AUTO, "it")
                       if p.codice == "SPAZIO"] for op in ("traduci", "riapplica", "ripristina")}
    finally:
        automatico.shutil.disk_usage = vero_spazio
    controlla("poco spazio (50 MB): avviso per il gioco e per i dati, si puo' continuare; non per Ripristina",
              len(spazio["traduci"]) == 2 and len(spazio["riapplica"]) == 2 and not spazio["ripristina"]
              and all(p.azione == "continua" and "50 MB" in p.messaggio for p in spazio["traduci"]))

    # translator paused: the local translator is offered (installing it, if missing)
    limitatore.Limitatore("google", log=None).bloccato(429, "prova")
    vero_stato = ollama.stato
    try:
        pausa = per_codice(automatico.controlli_preliminari(gioco, "google", "traduci", lingua.AUTO, "it"),
                           "TRADUTTORE_IN_PAUSA")
        ollama.stato = lambda: "pronto"
        pronta = per_codice(automatico.controlli_preliminari(gioco, "google", "traduci", lingua.AUTO, "it"),
                            "TRADUTTORE_IN_PAUSA")
        ollama.stato = vero_stato
        riapplica = codici(automatico.controlli_preliminari(gioco, "google", "riapplica", lingua.AUTO, "it"))
        deepl = codici(automatico.controlli_preliminari(gioco, "deepl", "traduci", lingua.AUTO, "it"))
    finally:
        ollama.stato = vero_stato
        limitatore.Limitatore("google", log=None).azzera()
    controlla("Google in pausa: 'Installa il traduttore locale' (non installato), 'Usa il traduttore locale' "
              "(installato); niente per Riapplica ne' per un altro traduttore",
              pausa and pausa.azione == "installa_locale" and "Google" in pausa.messaggio
              and "traduttore locale" in pausa.messaggio and pronta and pronta.azione == "usa_locale"
              and "TRADUTTORE_IN_PAUSA" not in riapplica and "TRADUTTORE_IN_PAUSA" not in deepl)

    # local translator chosen but not installed
    assente = per_codice(automatico.controlli_preliminari(gioco, "locale", "traduci", lingua.AUTO, "it"),
                         "LOCALE_ASSENTE")
    ollama.stato = lambda: "pronto"
    try:
        pronto = codici(automatico.controlli_preliminari(gioco, "locale", "traduci", lingua.AUTO, "it"))
    finally:
        ollama.stato = vero_stato
    controlla("traduttore locale non installato: 'Installa ora' prima di partire; installato: nessun avviso",
              assente and assente.azione == "installa_locale" and "LOCALE_ASSENTE" not in pronto
              and "continui senza" in assente.messaggio)
    # "Continue anyway" without a translator: the game is analyzed, but it stops before changing it (the official
    # language, if there is one, is activated without a translator)
    impronte = {n: open(os.path.join(r, n), "rb").read() for r, _d, f in os.walk(gioco) for n in f}
    try:
        automatico.analizza_e_traduci(gioco, "auto", "it", "locale", silenzio)
        errore = None
    except backend.ErroreBackend as e:
        errore = e
    controlla("continuare senza traduttore locale: errore 'traduttore non pronto' prima di toccare il gioco",
              errore is not None and getattr(errore, "codice", None) == "TRADUTTORE_ASSENTE"
              and {n: open(os.path.join(r, n), "rb").read() for r, _d, f in os.walk(gioco) for n in f} == impronte)


# --- the same game in another folder -----------------------------------------------------------------------------

def prova_stesso_gioco(base):
    unity_a = gioco_unity(os.path.join(base, "unity", "A", "Rubinite"), "Rubinite", "Cup Dog Games", "Rubinite")
    unity_b = gioco_unity(os.path.join(base, "unity", "B", "Rubinite Copia"), "Rubinite", "Cup Dog Games", "Rubinite")
    unity_c = gioco_unity(os.path.join(base, "unity", "C", "Altro"), "Rubinite", "Studio", "Altro Gioco")
    a, b, c = (automatico.identita(x) for x in (unity_a, unity_b, unity_c))
    controlla("identita Unity: azienda/prodotto ed eseguibili (%s)" % a,
              a.get("unity") == "Cup Dog Games/Rubinite" and a.get("exe") == ["rubinite.exe"])
    controlla("stesso gioco: stesso prodotto Unity (anche in una cartella con un altro nome); un prodotto diverso no, "
              "anche con lo stesso eseguibile", automatico.stesso_gioco(a, b) and not automatico.stesso_gioco(a, c))
    controlla("stesso gioco: Steam (appid), eseguibili, nome della cartella (anche con il '-2' del nome di un profilo, "
              "ma una cartella 'Gioco 2' e' il seguito)",
              automatico.stesso_gioco({"steam": "123"}, {"steam": "123", "exe": ["x.exe"]})
              and not automatico.stesso_gioco({"steam": "123", "exe": ["x.exe"]}, {"steam": "9", "exe": ["x.exe"]})
              and automatico.stesso_gioco({"exe": ["a.exe", "b.exe"]}, {"exe": ["b.exe"]})
              and not automatico.stesso_gioco({"exe": ["a.exe"]}, {"exe": ["b.exe"], "nome": "a"})
              and automatico.stesso_gioco({"nome": "lantern-keeper"}, {"nome": "lantern-keeper-2", "da_profilo": True})
              and automatico.stesso_gioco({"nome": "lantern-keeper-2", "da_profilo": True}, {"nome": "lantern-keeper"})
              and not automatico.stesso_gioco({"nome": "lantern-keeper"}, {"nome": "lantern-keeper-2"})
              and not automatico.stesso_gioco({"nome": "gioco"}, {"nome": "gioco"}))
    vero = steam.gioco_installato
    steam.gioco_installato = lambda cartella, steam=None: {"appid": "4242", "nome": "Prova"}
    try:
        con_steam = automatico.identita(unity_a)
        senza = automatico.identita(unity_a, steam=False)
    finally:
        steam.gioco_installato = vero
    controlla("identita: appid di Steam dal manifesto (non cercato con steam=False)",
              con_steam.get("steam") == "4242" and "steam" not in senza)
    # programs that many games share (.NET runtime, Ren'Py, DOSBox): they do not mean it is the same game
    uno = gioco_web(os.path.join(base, "net", "Uno", "Gioco"), "Primo Gioco.exe")
    due = gioco_web(os.path.join(base, "net", "Due", "Gioco"), "Secondo Gioco.exe")
    for d in (uno, due):
        os.makedirs(os.path.join(d, "lib", "py3-windows-x86_64"))
        for exe in ("createdump.exe", os.path.join("lib", "py3-windows-x86_64", "zsync.exe"), "DOSBox.exe"):
            open(os.path.join(d, exe), "wb").write(b"MZ")
    i_uno, i_due = automatico.identita(uno), automatico.identita(due)
    controlla("identita: createdump, zsync, DOSBox non contano; giochi diversi con quei programmi in comune non sono "
              "lo stesso gioco (%s)" % i_uno,
              i_uno.get("exe") == ["primo gioco.exe"] and not automatico.stesso_gioco(i_uno, i_due))

    # a copy of the already translated web game: its translation memory is offered (with the test translator, the
    # test one)
    originale = gioco_web(os.path.join(base, "web", "Originale", "Lantern Keeper"), "Lantern Keeper.exe")
    percorso, completato, _ = automatico.analizza_e_traduci(originale, "auto", "it", "finto", silenzio)
    with open(percorso, encoding="utf-8") as f:
        profilo = json.load(f)
    memoria = progetto.percorso_memoria(profilo, prova=True)
    voci = json.load(open(memoria, encoding="utf-8"))
    voci["Start a new game"] = "VALORE CORRETTO A MANO"
    json.dump(voci, open(memoria, "w", encoding="utf-8"), ensure_ascii=False)
    controlla("originale tradotto: l'identita resta nel profilo",
              completato and profilo.get("identita", {}).get("exe") == ["lantern keeper.exe"])
    copia = gioco_web(os.path.join(base, "web", "Copia", "Lantern Keeper"), "Lantern Keeper.exe")
    proposta = per_codice(automatico.controlli_preliminari(copia, "finto", "traduci", lingua.AUTO, "it"),
                          "STESSO_GIOCO")
    online = per_codice(automatico.controlli_preliminari(copia, "deepl", "traduci", lingua.AUTO, "it"),
                        "STESSO_GIOCO")
    altra_lingua = per_codice(automatico.controlli_preliminari(copia, "finto", "traduci", lingua.AUTO, "fr"),
                              "STESSO_GIOCO")
    controlla("copia: 'stesso gioco gia' tradotto' con il profilo dell'originale e i testi salvati; non con una memoria "
              "vuota (traduttore vero) ne' per un'altra lingua", proposta and proposta.azione == "riusa"
              and proposta.dati["profilo"] == percorso and proposta.dati["testi"] == len(voci)
              and not proposta.dati["spostato"] and "Riuso le sue traduzioni" in proposta.messaggio
              and online is None and altra_lingua is None)
    # a game profile from a previous version (without 'identita'): recognized by its folder, if it is still there
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in profilo.items() if k != "identita"}, f, ensure_ascii=False)
    try:
        vecchio = per_codice(automatico.controlli_preliminari(copia, "finto", "traduci", lingua.AUTO, "it"),
                             "STESSO_GIOCO")
    finally:
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump(profilo, f, ensure_ascii=False)
    controlla("profilo di una versione precedente, senza identita: riconosciuto dalla sua cartella",
              vecchio and vecchio.dati["profilo"] == percorso and not vecchio.dati["spostato"])
    righe = []
    nuovo, completato, _ = automatico.analizza_e_traduci(copia, "auto", "it", "finto", righe.append,
                                                         riusa=proposta.dati["profilo"])
    with open(nuovo, encoding="utf-8") as f:
        profilo_copia = json.load(f)
    memoria_copia = json.load(open(progetto.percorso_memoria(profilo_copia, prova=True), encoding="utf-8"))
    controlla("copia con 'Riusa': profilo nuovo con la memoria dell'originale (anche la correzione a mano) e "
              "l'origine segnata", nuovo != percorso and completato
              and memoria_copia.get("Start a new game") == "VALORE CORRETTO A MANO"
              and profilo_copia.get("traduzioni_da") == profilo["nome"]
              and any("Riuso le traduzioni" in r for r in righe))
    controlla("copia con il suo profilo: non si chiede piu'",
              "STESSO_GIOCO" not in codici(automatico.controlli_preliminari(copia, "finto", "traduci", lingua.AUTO,
                                                                             "it")))
    # copying the translations fails (disk full, file in use): what is missing is translated from scratch, without
    # stopping "Analyze and translate" and without leaving a half-written translation memory
    terza = gioco_web(os.path.join(base, "web", "Terza", "Lantern Keeper"), "Lantern Keeper.exe")
    vera_salva, rotture = automatico.file_condiviso.salva_unendo, []

    def salva_rotta(*a, **k):  # the first write (the copy of the translation memory) fails, the others succeed
        if not rotture:
            rotture.append(a[0])
            raise OSError(28, "No space left on device")
        return vera_salva(*a, **k)

    automatico.file_condiviso.salva_unendo = salva_rotta
    righe = []
    try:
        nuovo3, completato3, _ = automatico.analizza_e_traduci(terza, "auto", "it", "finto", righe.append,
                                                               riusa=percorso)
    finally:
        automatico.file_condiviso.salva_unendo = vera_salva
    with open(nuovo3, encoding="utf-8") as f:
        memoria3 = json.load(open(progetto.percorso_memoria(json.load(f), prova=True), encoding="utf-8"))
    controlla("copia delle traduzioni non riuscita: Analizza e traduci continua e traduce da capo, con l'avviso",
              completato3 and rotture and rotture[0].endswith(".json")
              and memoria3.get("Start a new game") != "VALORE CORRETTO A MANO"
              and memoria3.get("Start a new game") and any("Non sono riuscito a copiare" in r for r in righe))

    # a moved game: the recorded folder is no longer there, the game profile moves to the new folder
    spostato = os.path.join(automatico.cartella_profili(), "gioco-spostato.json")
    with open(spostato, "w", encoding="utf-8") as f:
        json.dump({"nome": "gioco-spostato", "cartella_gioco": os.path.join(base, "sparita", "Gioco").replace(
            os.sep, "/"), "lingua_origine": "auto", "lingua_destinazione": "it", "backend": "finto", "file": [],
            "identita": {"nome": "gioco", "exe": ["gioco spostato.exe"]}}, f)
    for prova in (False, True):
        with open(progetto.percorso_memoria({"nome": "gioco-spostato", "lingua_destinazione": "it"}, prova), "w",
                  encoding="utf-8") as f:
            json.dump({"Start a new game": "Nuova partita (spostato)"}, f)
    nuova_cartella = gioco_web(os.path.join(base, "web", "Nuovo Disco", "Gioco Spostato 2"), "Gioco Spostato.exe")
    profili_prima = len(list(automatico.elenco_profili()))
    proposta = per_codice(automatico.controlli_preliminari(nuova_cartella, "deepl", "traduci", lingua.AUTO, "it"),
                          "STESSO_GIOCO")
    controlla("gioco spostato: proposto con 'la cartella non c'e' piu'' (dall'identita salvata nel profilo)",
              proposta and proposta.dati["spostato"] and proposta.dati["profilo"] == spostato
              and "non c'è più" in proposta.messaggio)
    percorso_s, _, _ = automatico.analizza_e_traduci(nuova_cartella, "auto", "it", "finto", silenzio,
                                                     riusa=proposta.dati["profilo"])
    with open(spostato, encoding="utf-8") as f:
        dopo = json.load(f)
    controlla("gioco spostato con 'Riusa': stesso profilo e stessa memoria nella cartella nuova, nessun profilo nuovo",
              percorso_s == spostato and automatico._normalizza(dopo["cartella_gioco"]) == automatico._normalizza(
                  nuova_cartella)
              and dopo.get("cartella_precedente", "").endswith("sparita/Gioco")
              and len(list(automatico.elenco_profili())) == profili_prima
              and json.load(open(progetto.percorso_memoria(dopo, prova=True), encoding="utf-8")).get(
                  "Start a new game") == "Nuova partita (spostato)")


# --- default choices, graphics card, test of the local translator ---------------------------------------------

def prova_predefiniti(base):
    controlla("traduttore predefinito: quello locale anche se non e' installato ('da installare', 'Installa...')",
              ollama.stato() != "pronto" and backend.predefinito() == "locale")
    controlla("etichette: Google 'non ufficiale' (con l'avviso alla scelta), modelli con il compromesso",
              "non ufficiale" in gui.TRADUTTORI["google"] and "bloccarle" in gui.AVVISO_GOOGLE
              and "più lento" in gui.MODELLI_LOCALI["12b"])

    # graphics card: nvidia-smi, then the registry, then WMI; at WMI's 4 GB limit it cannot be told
    veri = ollama._nvidia_smi, ollama._registro_video, ollama._wmi_video
    chiamate = []
    try:
        for valori, atteso in (((8.0, 4.0, 2.0), 8.0), ((None, 6.0, 2.0), 6.0), ((None, None, 2.0), 2.0),
                               ((None, None, None), None)):
            ollama._MEMORIA_VIDEO.clear()
            ollama._nvidia_smi, ollama._registro_video, ollama._wmi_video = (
                (lambda v=v, i=i: chiamate.append(i) or v) for i, v in enumerate(valori))
            chiamate.clear()
            ottenuto = ollama.memoria_video()
            if ottenuto != atteso:
                raise AssertionError((valori, ottenuto))
        ordine = chiamate == [0, 1, 2]
        ollama._nvidia_smi = lambda: (_ for _ in ()).throw(OSError("guasto"))
        ollama._MEMORIA_VIDEO.clear()
        ollama._registro_video = lambda: 8.0
        robusto = ollama.memoria_video() == 8.0
        ok = True
    except AssertionError as e:
        print("   ", e)
        ok = ordine = robusto = False
    finally:
        ollama._nvidia_smi, ollama._registro_video, ollama._wmi_video = veri
        ollama._MEMORIA_VIDEO.clear()
    controlla("memoria video: nvidia-smi, poi registro di Windows, poi WMI; un errore non ferma nulla",
              ok and ordine and robusto)
    vero_run = ollama.subprocess.run
    risposte, comandi = {}, []
    ollama.subprocess.run = lambda comando, **_k: comandi.append(comando[0]) or types.SimpleNamespace(
        returncode=0, stdout=risposte["uscita"], stderr=b"")
    vero_programma = ollama._programma_nvidia_smi
    ollama._programma_nvidia_smi = lambda: sys.executable  # a file that exists, never executed (run is simulated)
    try:
        risposte["uscita"] = b"8192\r\n"
        nvidia = ollama._nvidia_smi()
        risposte["uscita"] = b"4293918720\r\n"
        wmi_limite = ollama._wmi_video()
        risposte["uscita"] = b"2147483648\r\n0\r\n"
        wmi_2 = ollama._wmi_video()
    finally:
        ollama.subprocess.run, ollama._programma_nvidia_smi = vero_run, vero_programma
    controlla("letture: nvidia-smi in MiB (8192 -> 8 GB), WMI al limite di 4 GB = non si sa, WMI 2 GB; PowerShell "
              "con il percorso intero (%s)" % comandi[1:2],
              nvidia == 8.0 and wmi_limite is None and wmi_2 == 2.0 and all(os.path.isabs(c) for c in comandi))
    # nvidia-smi never from the current folder (a program with that name there must not start): System32, NVSMI, PATH
    corrente, altrove = os.path.join(base, "nvsmi-corrente"), os.path.join(base, "nvsmi-path")
    for d in (corrente, altrove):
        os.makedirs(d)
        open(os.path.join(d, "nvidia-smi.exe"), "wb").close()
    variabili = {k: os.environ.get(k) for k in ("SystemRoot", "ProgramFiles", "PATH")}
    cwd = os.getcwd()
    try:
        os.environ.update(SystemRoot=os.path.join(base, "windows"), ProgramFiles=os.path.join(base, "programmi"))
        os.chdir(corrente)
        os.environ["PATH"] = os.pathsep.join([".", corrente])
        solo_corrente = ollama._programma_nvidia_smi()
        os.environ["PATH"] = os.pathsep.join([".", corrente, altrove])
        nel_path = ollama._programma_nvidia_smi()
    finally:
        os.chdir(cwd)
        for k, v in variabili.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    controlla("nvidia-smi: mai quello della cartella corrente, si' quello nel PATH (%s, %s)"
              % (solo_corrente, nel_path), solo_corrente is None and nel_path and os.path.dirname(nel_path) == altrove)
    consigli = {gb: ollama.modello_consigliato(gb)[0] for gb in (8.0, 7.9, 6.0, 4.0)}
    ollama._MEMORIA_VIDEO[:] = [None]
    try:
        senza = ollama.modello_consigliato()
    finally:
        ollama._MEMORIA_VIDEO.clear()
    controlla("modello consigliato: 12B da 8 GB (anche 7,9), 4B sotto e se la scheda non si conosce (%s)" % consigli,
              consigli == {8.0: "12b", 7.9: "12b", 6.0: "4b", 4.0: "4b"} and senza[0] == "4b"
              and "8 GB" in ollama.modello_consigliato(8.0)[1] and "Veloce" in senza[1])
    controlla("avviso prima di scaricare: grafica integrata o scheda vecchia -> lento sul processore (con DeepL come "
              "alternativa); poca RAM -> avviso; scheda da 8 GB, 16 GB di RAM o PC sconosciuto -> nessun avviso",
              "processore" in ollama.avviso_prestazioni(0.5, 16) and "DeepL" in ollama.avviso_prestazioni(0.5, 16)
              and "RAM" in ollama.avviso_prestazioni(8, 6) and ollama.avviso_prestazioni(8, 16) == ""
              and ollama.avviso_prestazioni(3.9, 16) == "")

    # command line: "locale" without installation tells the recommended model and the command to install it;
    # "--installa" also does the test sentence (code 2 if it fails)
    from patchsmith import cli
    uscita = io.StringIO()
    ollama._MEMORIA_VIDEO[:] = [8.0]
    try:
        with contextlib.redirect_stdout(uscita):
            codice_stato = cli.main(["locale"])
    finally:
        ollama._MEMORIA_VIDEO.clear()
    testo = uscita.getvalue()
    veri = ollama.descrizione_download, ollama.installa, backend.prova_locale
    prove = []
    ollama.descrizione_download = lambda scelti: ["finto"]
    ollama.installa = lambda scelti, log=print: True
    backend.prova_locale = lambda modello=None, log=print, **_k: prove.append(modello) or {
        "ok": False, "messaggio": "la prova di traduzione non e' riuscita: prova"}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            codice_prova = cli.main(["locale", "--installa", "12b"])
    finally:
        ollama.descrizione_download, ollama.installa, backend.prova_locale = veri
    controlla("riga di comando 'locale': modello consigliato (12B con 8 GB) e comando; '--installa' fa la frase di "
              "prova e esce con 2 se non riesce", codice_stato == 0 and "Qualità (12B)" in testo
              and "--install 12b (oppure 4b" in testo and codice_prova == 2 and prove == ["12b"])

    # test of the local translator after installation
    server = ServerFinto()
    r = backend.prova_locale("4b", silenzio, server=server)
    controlla("prova del traduttore locale: tradotto sulla scheda video, con i tempi (%s)" % r["messaggio"],
              r["ok"] and r["scheda"] == 1.0 and r["traduzione"] == "Inizia la partita"
              and "sulla scheda video" in r["messaggio"] and "Start game" in r["messaggio"] and r["secondi"] is not None)
    r = backend.prova_locale("4b", silenzio, server=ServerFinto(vram=0))
    controlla("prova: solo processore -> 'sara' lento' e il consiglio", r["ok"] and r["scheda"] == 0
              and "solo con il processore" in r["messaggio"])
    r = backend.prova_locale("4b", silenzio, server=ServerFinto(risposta="Start game"))
    controlla("prova: frase rimasta in originale -> non riuscita, senza errori", not r["ok"]
              and "rimasto in originale" in r["messaggio"])
    r = backend.prova_locale("4b", silenzio, server=ServerFinto(errore=ollama.ErroreLocale("non parte")))
    controlla("prova: server che non parte -> non riuscita, con il motivo, senza eccezioni",
              not r["ok"] and "non parte" in r["messaggio"])

    # the limiter recommends the local translator
    messaggio = limitatore.Limitatore("google", log=None).bloccato(429, "prova")
    limitatore.Limitatore("google", log=None).azzera()
    controlla("blocco di Google: il messaggio consiglia il traduttore locale, senza comandi",
              "traduttore locale" in messaggio and "smith.py" not in messaggio)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    automatico.CARTELLA_PROFILI = None
    ollama.cartella = lambda: os.path.join(base, "ollama")  # local translator not installed
    steam.gioco_installato = lambda cartella, steam=None: None  # no real Steam
    # the free space of the tests' disk must not count (the space test simulates it separately)
    shutil.disk_usage = lambda _p: types.SimpleNamespace(total=10 ** 13, used=0, free=10 ** 12)
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    prova_controlli(base)
    prova_stesso_gioco(base)
    prova_predefiniti(base)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
