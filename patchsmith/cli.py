"""Command-line interface."""
import argparse
import json
import os
import sys

from . import (__version__, adapter_unity, adapter_unreal, adapter_xna, auto as automatico, language as lingua,
               detection as rilevamento, scan as scansione)
from . import components as componenti, dotnet, project as progetto, ui_language as interfaccia, xnb
from .backend import BACKEND, ErroreBackend, crea_backend, predefinito
from .project import Progetto
from .text import DETTAGLIO, leggibile, slug


def _log(messaggio=""):
    print(interfaccia.traduci(leggibile(str(messaggio)).replace(DETTAGLIO, "")), flush=True)


def _lingua_interfaccia():
    """The window's language (settings.json, otherwise the Windows one): also for the help and the messages here."""
    try:
        with open(progetto.cartella_dati("settings.json"), encoding="utf-8") as f:
            scelta = json.load(f).get("interfaccia")
    except (OSError, ValueError, AttributeError):
        scelta = None
    interfaccia.imposta(scelta or interfaccia.predefinita())


def _traduci_aiuto(parser):
    """The help (-h) in the interface language: description and explanations of commands and options."""
    parser.description = interfaccia.traduci(parser.description)
    for azione in parser._actions:
        azione.help = interfaccia.traduci(azione.help)
        if isinstance(azione, argparse._SubParsersAction):
            for scelta in azione._choices_actions:
                scelta.help = interfaccia.traduci(scelta.help)
            for sotto in {id(p): p for p in azione.choices.values()}.values():  # (the aliases: same parser)
                _traduci_aiuto(sotto)


def _profilo(valore):
    """Accepts a game profile path or just the name (looked up in profiles/). A path that no longer exists (e.g.
    profili\\<gioco>.json, the former name of the folder, written in the translator's command inside an already
    translated game) counts for its name."""
    if os.path.isfile(valore):
        return valore
    if os.path.isdir(valore):
        raise SystemExit("Serve un profilo, non una cartella: %s" % valore)
    nome = os.path.splitext(os.path.basename(valore))[0] if valore.lower().endswith(".json") else valore
    candidato = os.path.join(automatico.cartella_profili(), nome + ".json")
    if os.path.exists(candidato):
        return candidato
    raise SystemExit("Profilo non trovato: %s" % valore)


def cmd_rileva(a):
    rilevamento.stampa(rilevamento.rileva(a.cartella), _log)


def cmd_scansiona(a):
    cartella = os.path.abspath(a.cartella)
    _log("Motore:")
    esito = rilevamento.rileva(cartella)
    rilevamento.stampa(esito, lambda m: _log("  " + m))
    _log()
    candidati, altri = scansione.scansiona(cartella, _log)
    scansione.stampa(candidati, altri, _log, dettaglio=a.dettaglio)
    esistente = automatico.trova_profilo(cartella)
    if a.nome:
        nome = slug(a.nome)
    elif esistente:
        nome = os.path.splitext(os.path.basename(esistente))[0]
    else:
        nome = automatico.nome_per_cartella(cartella)
    _, web = automatico.gioco_supportato(esito)
    profilo = scansione.crea_profilo(nome, cartella, candidati, a.da, a.a, a.backend or predefinito(),
                                     esito["radici_web"] if web else (), motore=esito.get("motore_principale"),
                                     log=_log)
    if not profilo["file"]:
        _log("\nNessun file sopra soglia: profilo non creato.")
        return 1
    percorso = os.path.join(automatico.cartella_profili(), nome + ".json")
    if os.path.exists(percorso):
        with open(percorso, encoding="utf-8") as f:
            registrata = json.load(f).get("cartella_gioco", "")
        if os.path.normcase(os.path.abspath(registrata)) != os.path.normcase(cartella):
            _log("\nIl nome '%s' e' gia' usato per un altro gioco (%s): scegline un altro con --name."
                 % (nome, registrata))
            return 1
        if not a.sovrascrivi:
            _log("\nIl profilo %s esiste gia' (usa --overwrite per rigenerarlo)." % percorso)
            return 0
    scansione.salva_json(percorso, profilo)
    _log("\nProfilo creato: %s" % percorso)
    for f in profilo["file"]:
        if f.get("formato") == "web-ui":
            _log("  %s: interfaccia (menu)" % f["percorso"])
        elif f.get("formato") == "csv":
            _log("  %s: tabella dei testi, colonna %s" % (f["percorso"], f.get("colonna")))
        else:
            _log("  %s: %d schemi inclusi, %d esclusi" % (f["percorso"], len(f["includi"]), len(f["escludi"])))
    _log("Controllalo (facoltativo), poi:  python -I smith.py translate %s" % nome)
    return 0


def _profilo_motore(percorso, motore):
    with open(percorso, encoding="utf-8") as f:
        profilo = json.load(f)
    return profilo if profilo.get("motore") == motore else None


def _profilo_unity(percorso):
    """The game profile if it belongs to a Unity game (translated by XUnity), otherwise None."""
    return _profilo_motore(percorso, "unity")


def _profilo_unreal(percorso):
    return _profilo_motore(percorso, "unreal")


def _profilo_xna(percorso):
    return _profilo_motore(percorso, "xna")


def cmd_traduci(a):
    percorso = _profilo(a.profilo)
    with open(percorso, encoding="utf-8") as f:
        backend_profilo = json.load(f).get("backend", "google")
    unity = _profilo_unity(percorso)
    if unity:  # installs/updates XUnity and pre-translates the game's texts
        return 0 if automatico.unity_completo(percorso, _log, rete=True, backend=a.backend or backend_profilo) else 2
    if _profilo_unreal(percorso):
        return 0 if automatico.unreal_completo(percorso, _log, rete=True, backend=a.backend or backend_profilo) else 2
    if _profilo_xna(percorso):
        return 0 if automatico.xna_completo(percorso, _log, rete=True, backend=a.backend or backend_profilo) else 2
    with open(percorso, encoding="utf-8") as f:
        nome_backend = a.backend or json.load(f).get("backend", "google")
    p = Progetto(percorso, _log, prova=(nome_backend == "finto"))
    return 0 if p.traduci(crea_backend(nome_backend, _log)) else 2


def cmd_applica(a):
    percorso = _profilo(a.profilo)
    if _profilo_unity(percorso):  # for Unity: components already downloaded and translations in memory, no network
        return 0 if automatico.unity_completo(percorso, _log, rete=False) else 2
    if _profilo_unreal(percorso):
        return 0 if automatico.unreal_completo(percorso, _log, rete=False,
                                               backend="finto" if a.prova else "google") else 2
    if _profilo_xna(percorso):
        return 0 if automatico.xna_completo(percorso, _log, rete=False, backend="finto" if a.prova else "google") else 2
    return 0 if Progetto(percorso, _log, prova=a.prova).applica() else 2


def cmd_ripristina(a):
    if os.path.isdir(a.profilo):  # game folder: everything, including the language set by PatchSmith
        automatico.ripristina(a.profilo, _log)
        return 0
    percorso = _profilo(a.profilo)
    with open(percorso, encoding="utf-8") as f:
        cartella = json.load(f).get("cartella_gioco")
    if cartella and os.path.isdir(cartella):  # everything, including the language set or added by PatchSmith
        automatico.ripristina(cartella, _log)
        return 0
    Progetto(percorso, _log).ripristina()


def cmd_stato(a):
    percorso = _profilo(a.profilo)
    unity = _profilo_unity(percorso)
    if unity:
        adapter_unity.stato(unity["cartella_gioco"], unity["nome"], unity["lingua_destinazione"], _log)
        return 0
    unreal = _profilo_unreal(percorso)
    if unreal:
        adapter_unreal.stato(unreal["cartella_gioco"], _log)
        return 0
    xna = _profilo_xna(percorso)
    if xna:
        adapter_xna.stato(xna["cartella_gioco"], _log)
        return 0
    Progetto(percorso, _log).mostra_stato(a.esempi)


def cmd_auto(a):
    percorso, completato, esito = automatico.analizza_e_traduci(
        a.cartella, a.da, a.a, a.backend or predefinito(), _log, forza=a.forza, rigenera=a.rigenera,
        traduci_comunque=a.traduci_comunque, consenso=a.modifica_eseguibili)
    if not percorso and esito.get("sperimentale_possibile"):
        _log("Per provarci comunque: aggiungi --force")
    if not percorso and esito.get("serve_consenso"):
        _log("Per attivarla: ripeti con --modify-executables (il consenso vale poi per questo gioco)")
    elif not percorso and esito.get("lingua_ufficiale"):
        if a.modifica_eseguibili and esito.get("lingua_impostata") is False:
            return 2  # activation requested but failed (the reason has already been written)
        if esito.get("motore_supportato", True):
            _log("Per una traduzione automatica comunque: aggiungi --translate-anyway")
        return 0  # the official language has been handled: not an error
    return 0 if completato else 2


def cmd_controlla(a):
    """After-play check (checks.py) of one game, or of all the games with the translation installed.
    Exit code: 0 if nothing is left to do, 2 if something is left (including a fix postponed because the game was
    open, or not made because of --dry-run), 1 if the check of a game failed."""
    from . import checks as controllo
    if a.cartella:
        if os.path.isdir(a.cartella):
            percorso = automatico.trova_profilo(a.cartella)
            if not percorso:
                _log("Nessuna traduzione di PatchSmith registrata per questa cartella: niente da controllare.")
                return 0
        else:
            percorso = _profilo(a.cartella)
        percorsi = [percorso]
    else:
        percorsi = [p for p, profilo in automatico.elenco_profili()  # (not the test profiles: fake translator)
                    if profilo.get("cartella_gioco") and os.path.isdir(profilo["cartella_gioco"])
                    and profilo.get("backend") != "finto"]
    resta = errori = False
    for percorso in percorsi:
        try:
            problemi = controllo.controlla(percorso, applica=not a.solo_leggi, log=_log, traduttore=a.backend)
        except Exception as e:  # noqa: BLE001 - a broken game profile does not stop the check of the other games
            _log("ERRORE nel controllo di %s (%s: %s)" % (os.path.basename(percorso), type(e).__name__, e))
            errori = True
            continue
        resta = resta or any(p.stato in controllo.DA_FARE + ("rimandato", "da_correggere") for p in problemi)
        _log()
    if errori:
        return 1
    if a.solo_leggi:
        _log("(sola lettura: nessuna correzione fatta)")
    return 2 if resta else 0


def cmd_servizi(a):
    """Status of the online translators (pauses after a block) and, with --reset, reactivation of one."""
    from .limiter import NOMI, Limitatore
    if a.azzera:
        Limitatore(a.azzera, _log).azzera()
        _log("%s: riattivato. Attenzione: se il servizio rifiuta ancora le richieste, insistere puo' allungare "
             "il blocco." % NOMI.get(a.azzera, a.azzera))
        return 0
    for servizio in ("google", "deepl", "mymemory"):
        l = Limitatore(servizio, _log)
        _log("%-9s %s" % (NOMI[servizio] + ":", l.pausa() or "disponibile"))
        blocco = l.blocco_attuale()
        if blocco and blocco.get("disattivato"):
            _log("          per riattivarlo: python -I smith.py services --reset %s" % servizio)
    return 0


def cmd_servizio_xunity(a):
    """Translator of new lines for XUnity (started by the PatchSmith plugin when the game starts, without a
    window): writes to components/ollama/servizio-xunity.log; the requested lines go into the journal
    memory/<gioco>.richieste.jsonl."""
    import datetime
    from . import ollama, xunity_service as servizio_xunity
    registro = os.path.join(ollama.cartella(), "servizio-xunity.log")
    os.makedirs(os.path.dirname(registro), exist_ok=True)
    try:
        if os.path.getsize(registro) > 2_000_000:
            os.replace(registro, registro + ".1")
    except OSError:
        pass
    with open(registro, "a", encoding="utf-8") as f:
        def log(messaggio=""):
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), messaggio))
            f.flush()
            if a.mostra and sys.stdout:
                _log(messaggio)
        return servizio_xunity.Servizio(_profilo(a.profilo), a.porta, a.pid, log).avvia()


def cmd_locale(a):
    """Status of the local translator and, with --install, installation (downloads: they only start this way)."""
    from . import ollama
    if a.installa:
        righe = ollama.descrizione_download(a.installa)
        if not righe:
            _log("Gia' installato.")
            return 0
        _log("Scarico in %s:\n- %s" % (ollama.cartella(), "\n- ".join(righe)))
        try:
            ollama.installa(a.installa, _log)
        except ollama.ErroreLocale as e:
            _log("Installazione non riuscita: %s" % e)
            return 2
        from .backend import prova_locale
        _log("Prova del traduttore locale: traduco \"Start game\"...")
        prova = prova_locale(a.installa[0], _log)
        _log("Traduttore locale: %s" % prova["messaggio"])
        if not prova["ok"]:
            return 2
    _log("Traduttore locale: %s (in %s)" % (ollama.stato(), ollama.cartella()))
    for k, m in ollama.MODELLI.items():
        _log("  %-4s %-30s %s" % (k, m["etichetta"], "scaricato" if ollama.modello_presente(k) else
                                  "da scaricare (%.1f GB)" % m["gb"]))
    if ollama.stato() != "pronto":
        chiave, motivo = ollama.modello_consigliato()
        _log(motivo)
        _log("Per installarlo: python -I smith.py local --install %s (oppure %s, o entrambi)"
             % (chiave, "4b" if chiave == "12b" else "12b"))
    return 0


def cmd_interfaccia(a):
    """Interface languages: list, or --create <codice> to translate the program's texts with the local translator
    (those already in the file stay: fixes made by hand are not lost)."""
    if not a.crea:
        for codice, info in interfaccia.lingue().items():
            _log("%s  %s" % (codice, info["nome"]))
        return 0
    from .backend import Locale
    bandiera = json.loads(a.bandiera) if a.bandiera else {}
    traduttore = Locale(a.modello, log=_log)
    try:
        n = interfaccia.crea_lingua(a.crea, a.nome or a.crea, traduttore, _log, bandiera)
    finally:
        traduttore.chiudi()  # frees the graphics card and closes the translator's server
    _log("Aggiunti %d testi in patchsmith/ui_language/%s.json: rivedili a mano." % (n, a.crea))
    return 0


def cmd_pacchetto(a):
    """The package to publish: folder and zip with PatchSmith.exe and the portable Python (see package.py)."""
    from . import package as pacchetto
    try:
        pacchetto.prepara(a.uscita, a.python, _log)
    except pacchetto.ErrorePacchetto as e:
        _log("ERRORE: %s" % e)
        return 1
    return 0


def cmd_gui(a):
    from .gui import main as avvia
    avvia()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    progetto.migra_dati()  # data with the former Italian names (game profiles, memory...) move to the English ones
    _lingua_interfaccia()
    ap = argparse.ArgumentParser(
        prog="smith",
        description="PatchSmith %s: traduce i testi dei giochi direttamente nei file del gioco." % __version__)
    sub = ap.add_subparsers(dest="comando", required=True)
    # commands and options in English; the former Italian names stay as aliases (dest: the former name)
    lingua_gioco = dict(dest="da", metavar="LANG", default="auto", type=lingua.codice,
                        help="lingua del gioco (predefinita: auto = rilevata)")
    lingua_arrivo = dict(dest="a", metavar="LANG", default=lingua.lingua_di_windows(), type=lingua.codice,
                         help="lingua di destinazione (predefinita: quella di Windows)")

    s = sub.add_parser("auto", help="tutto in automatico: rileva, trova i testi, traduce e applica")
    s.add_argument("cartella", metavar="folder")
    s.add_argument("--from", "--da", **lingua_gioco)
    s.add_argument("--to", "--a", **lingua_arrivo)
    s.add_argument("--backend", choices=sorted(BACKEND),
                   help="traduttore (predefinito: locale; se non e' installato, dice come installarlo)")
    s.add_argument("--force", "--forza", dest="forza", action="store_true",
                   help="prova anche se il motore non e' ancora supportato")
    s.add_argument("--regenerate", "--rigenera", dest="rigenera", action="store_true", help="ricrea il profilo da zero")
    s.add_argument("--translate-anyway", "--traduci-comunque", dest="traduci_comunque", action="store_true",
                   help="traduce anche se il gioco ha gia' la lingua di arrivo ufficiale")
    s.add_argument("--modify-executables", "--modifica-eseguibili", dest="modifica_eseguibili", action="store_true",
                   help="consente di modificare gli eseguibili del gioco quando serve per attivare la sua lingua "
                        "ufficiale (con backup; il consenso resta valido per quel gioco)")
    s.set_defaults(fn=cmd_auto)

    s = sub.add_parser("check", aliases=["controlla"],
                       help="controllo dopo aver giocato: crash, traduttore delle frasi nuove, file rimessi dal gioco; "
                            "corregge da solo quello che e' sicuro")
    s.add_argument("cartella", nargs="?", metavar="folder",
                   help="cartella del gioco o nome del profilo (predefinito: tutti i giochi)")
    s.add_argument("--dry-run", "--solo-leggi", dest="solo_leggi", action="store_true",
                   help="dice cosa farebbe, senza correggere ne' scrivere nulla")
    s.add_argument("--backend", choices=sorted(BACKEND),
                   help="traduttore per le frasi rimaste in originale mentre giocavi (predefinito: quello del gioco)")
    s.set_defaults(fn=cmd_controlla)

    s = sub.add_parser("services", aliases=["servizi"], help="stato dei traduttori online (pause dopo un blocco)")
    s.add_argument("--reset", "--azzera", dest="azzera", choices=["google", "deepl", "mymemory"],
                   help="riattiva un traduttore in pausa")
    s.set_defaults(fn=cmd_servizi)

    s = sub.add_parser("local", aliases=["locale"],
                       help="traduttore locale (Ollama + TranslateGemma): stato e installazione")
    s.add_argument("--install", "--installa", dest="installa", nargs="+", choices=["4b", "12b"],
                   help="scarica e installa Ollama e i modelli scelti (4b: 3,3 GB, 12b: 8,1 GB)")
    s.set_defaults(fn=cmd_locale)

    s = sub.add_parser("xunity-service", aliases=["servizio-xunity"],
                       help="traduttore delle frasi nuove per XUnity mentre si gioca (lo avvia il gioco)")
    s.add_argument("--profile", "--profilo", dest="profilo", metavar="PROFILE", required=True,
                   help="nome o percorso del profilo del gioco")
    s.add_argument("--port", "--porta", dest="porta", metavar="PORT", type=int,
                   help="porta su 127.0.0.1 (predefinita: quella del gioco)")
    s.add_argument("--pid", type=int, help="processo del gioco: alla sua chiusura il servizio si ferma")
    s.add_argument("--show", "--mostra", dest="mostra", action="store_true", help="scrive il registro anche qui")
    s.set_defaults(fn=cmd_servizio_xunity)

    s = sub.add_parser("interface", aliases=["interfaccia"],
                       help="lingue dell'interfaccia: elenco, o --create per aggiungerne una")
    s.add_argument("--create", "--crea", dest="crea", metavar="CODE",
                   help="crea o completa la lingua (es. en, fr) con il traduttore locale")
    s.add_argument("--name", "--nome", dest="nome", metavar="NAME",
                   help="nome della lingua nella lingua stessa (es. English, Français)")
    s.add_argument("--model", "--modello", dest="modello", default="4b", choices=["4b", "12b"],
                   help="modello del traduttore locale")
    s.add_argument("--flag", "--bandiera", dest="bandiera", metavar="JSON",
                   help='bandiera in JSON, es. {"verticali": ["#0055a4", "#ffffff", "#ef4135"]}')
    s.set_defaults(fn=cmd_interfaccia)

    s = sub.add_parser("package", aliases=["pacchetto"],
                       help="prepara il pacchetto da pubblicare (zip con PatchSmith.exe e Python portatile)")
    s.add_argument("--output", "--uscita", dest="uscita", metavar="FOLDER",
                   help="cartella del pacchetto (per difetto PatchSmith-rilascio accanto al programma)")
    s.add_argument("--python", metavar="FOLDER",
                   help="cartella di un Python installato da usare (per difetto quello attuale)")
    s.set_defaults(fn=cmd_pacchetto)

    s = sub.add_parser("gui", help="apre l'interfaccia grafica")
    s.set_defaults(fn=cmd_gui)

    s = sub.add_parser("detect", aliases=["rileva"], help="riconosce il motore del gioco")
    s.add_argument("cartella", metavar="folder")
    s.set_defaults(fn=cmd_rileva)

    s = sub.add_parser("scan", aliases=["scansiona"], help="trova i file con testo e crea il profilo del gioco")
    s.add_argument("cartella", metavar="folder")
    s.add_argument("--name", "--nome", dest="nome", metavar="NAME",
                   help="nome del profilo (predefinito: nome della cartella)")
    s.add_argument("--from", "--da", **lingua_gioco)
    s.add_argument("--to", "--a", **lingua_arrivo)
    s.add_argument("--backend", choices=sorted(BACKEND),
                   help="traduttore (predefinito: locale; se non e' installato, dice come installarlo)")
    s.add_argument("--detail", "--dettaglio", dest="dettaglio", metavar="FILE",
                   help="mostra gli schemi di testo di un file (percorso relativo)")
    s.add_argument("--overwrite", "--sovrascrivi", dest="sovrascrivi", action="store_true",
                   help="rigenera il profilo se esiste")
    s.set_defaults(fn=cmd_scansiona)

    s = sub.add_parser("translate", aliases=["traduci"],
                       help="traduce i testi mancanti e li scrive nel gioco (con backup)")
    s.add_argument("profilo", metavar="profile", help="nome o percorso del profilo")
    s.add_argument("--backend", choices=sorted(BACKEND), help="sostituisce il backend del profilo")
    s.set_defaults(fn=cmd_traduci)

    s = sub.add_parser("apply", aliases=["applica"], help="riscrive nel gioco le traduzioni in memoria (senza rete)")
    s.add_argument("profilo", metavar="profile")
    s.add_argument("--test", "--prova", dest="prova", action="store_true",
                   help="usa la memoria delle traduzioni di prova")
    s.set_defaults(fn=cmd_applica)

    s = sub.add_parser("restore", aliases=["ripristina"],
                       help="rimette i file originali del gioco (e la lingua, se impostata da PatchSmith)")
    s.add_argument("profilo", metavar="profile", help="profilo oppure cartella del gioco")
    s.set_defaults(fn=cmd_ripristina)

    s = sub.add_parser("status", aliases=["stato"], help="mostra quanti testi sono tradotti e qualche esempio")
    s.add_argument("profilo", metavar="profile")
    s.add_argument("--examples", "--esempi", dest="esempi", metavar="N", type=int, default=8)
    s.set_defaults(fn=cmd_stato)

    _traduci_aiuto(ap)
    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (adapter_unity.ErroreUnity, adapter_unreal.ErroreUnreal, adapter_xna.ErroreXna, componenti.ErroreComponente,
            dotnet.ErroreDotnet, xnb.ErroreXnb, ErroreBackend,
            RuntimeError, ValueError) as e:
        _log("ERRORE: %s" % e)
        if getattr(e, "comando", None):  # the messages talk about the interface buttons: here the command
            _log("Da riga di comando: %s" % e.comando)
        return 1
